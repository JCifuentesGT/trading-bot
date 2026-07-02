"""
optimize.py
Grid search sobre parámetros de la estrategia usando datos históricos reales.

Uso:
    python optimize.py              # búsqueda completa (144 combinaciones)
    python optimize.py --top 20    # mostrar top 20 en vez de top 10
    python optimize.py --days 180  # usar solo últimos 6 meses

Rankea por Profit Factor con filtro mínimo de 30 trades.
Al final actualiza strategy/optimized_params.json con los mejores parámetros.
"""

import argparse
import itertools
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import download_klines, run_backtest, compute_metrics, SYMBOLS, INITIAL_CAPITAL

# ── Espacio de búsqueda ────────────────────────────────────────────────────────
PARAM_GRID = {
    "adx_min":           [20, 25, 30, 35],
    "rsi_trigger":       [45, 50, 55],
    "atr_multiplier_sl": [1.0, 1.5, 2.0],
    "atr_multiplier_tp": [2.0, 2.5, 3.0, 3.5],
}
# EMA fijos — cambiarlos requeriría re-calcular sesgo en 1h también
FIXED_PARAMS = {"ema_fast": 20, "ema_slow": 50}

MIN_TRADES = 30   # descartar combinaciones con muy pocos trades (sin significancia)


def _all_combinations() -> list[dict]:
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    return [{**FIXED_PARAMS, **dict(zip(keys, combo))} for combo in itertools.product(*values)]


def _winrate_threshold(sl_mult: float, tp_mult: float) -> float:
    """Win rate mínimo teórico para breakeven con este ratio R:R."""
    rr = tp_mult / sl_mult
    return 1 / (1 + rr)


def run_optimization(days: int, top_n: int):
    combos = _all_combinations()
    total  = len(combos)
    print(f"Optimizando {total} combinaciones × {len(SYMBOLS)} pares = {total * len(SYMBOLS)} backtests")
    print(f"Usando caché de datos ({days} días) — si no existe, descarga primero con backtest.py\n")

    # Cargar datos una sola vez
    print("Cargando datos históricos...")
    data = {}
    for sym in SYMBOLS:
        df15 = download_klines(sym, "15", days, use_cache=True)
        df1h = download_klines(sym, "60", days, use_cache=True)
        if df15.empty or df1h.empty:
            print(f"  {sym} — sin datos, saltando")
            continue
        data[sym] = (df15, df1h)
    print(f"  {len(data)} pares cargados\n")

    if not data:
        print("Sin datos disponibles. Ejecuta primero: python backtest.py --days", days)
        return

    results = []
    cap_per_sym = INITIAL_CAPITAL / len(data)

    for i, params in enumerate(combos):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{total}] ADX>={params['adx_min']} RSI{params['rsi_trigger']} "
                  f"SL{params['atr_multiplier_sl']}x TP{params['atr_multiplier_tp']}x ...", flush=True)

        sym_results = []
        for sym, (df15, df1h) in data.items():
            r = run_backtest(sym, df15, df1h, params, cap_per_sym)
            r["symbol"] = sym
            sym_results.append(r)

        m = compute_metrics(sym_results, INITIAL_CAPITAL)
        if "error" in m:
            continue
        if m["total_trades"] < MIN_TRADES:
            continue

        # Calcular expected value por trade
        ev = (m["win_rate"] / 100 * m["avg_win"]) + ((1 - m["win_rate"] / 100) * m["avg_loss"])

        results.append({
            "params":        params,
            "profit_factor": m["profit_factor"],
            "sharpe":        m["sharpe"],
            "win_rate":      m["win_rate"],
            "total_trades":  m["total_trades"],
            "roi_pct":       m["roi_pct"],
            "max_drawdown":  m["max_drawdown"],
            "total_pnl":     m["total_pnl"],
            "avg_win":       m["avg_win"],
            "avg_loss":      m["avg_loss"],
            "ev_per_trade":  round(ev, 4),
        })

    if not results:
        print("\nNinguna combinación superó el mínimo de trades. Prueba con --days mayor.")
        return

    # Rankear: primero profit_factor, desempate por sharpe
    results.sort(key=lambda x: (x["profit_factor"], x["sharpe"]), reverse=True)

    print(f"\n{'='*72}")
    print(f"TOP {min(top_n, len(results))} COMBINACIONES (de {len(results)} válidas)")
    print(f"{'='*72}")
    header = f"{'#':>3} {'PF':>5} {'Sharpe':>7} {'WR%':>5} {'ROI%':>6} {'DD%':>6} {'Trades':>7}  Params"
    print(header)
    print("-" * 72)

    top_results = results[:top_n]
    for rank, r in enumerate(top_results, 1):
        p = r["params"]
        params_str = (f"ADX>={p['adx_min']} RSI{p['rsi_trigger']} "
                      f"SL{p['atr_multiplier_sl']}x TP{p['atr_multiplier_tp']}x")
        flag = " ◄ MEJOR" if rank == 1 else ""
        print(f"{rank:>3} {r['profit_factor']:>5.2f} {r['sharpe']:>7.2f} "
              f"{r['win_rate']:>5.1f} {r['roi_pct']:>+6.1f} {r['max_drawdown']:>6.1f} "
              f"{r['total_trades']:>7}  {params_str}{flag}")

    # Detalle del mejor
    best = results[0]
    bp   = best["params"]
    print(f"\n{'='*72}")
    print("MEJOR CONFIGURACIÓN — DETALLE")
    print(f"{'='*72}")
    print(f"  ADX mínimo:    {bp['adx_min']}")
    print(f"  RSI trigger:   {bp['rsi_trigger']}")
    print(f"  SL multiplier: {bp['atr_multiplier_sl']}x ATR")
    print(f"  TP multiplier: {bp['atr_multiplier_tp']}x ATR")
    print(f"  Ratio R:R:     1:{bp['atr_multiplier_tp']/bp['atr_multiplier_sl']:.1f}")
    print(f"  WR breakeven:  {_winrate_threshold(bp['atr_multiplier_sl'], bp['atr_multiplier_tp'])*100:.1f}%  (actual: {best['win_rate']:.1f}%)")
    print()
    print(f"  Profit Factor: {best['profit_factor']:.2f}")
    print(f"  Sharpe:        {best['sharpe']:.2f}")
    print(f"  ROI ({days}d):  {best['roi_pct']:+.1f}%")
    print(f"  Max Drawdown:  {best['max_drawdown']:.1f}%")
    print(f"  Trades:        {best['total_trades']}")
    print(f"  EV por trade:  ${best['ev_per_trade']:+.4f}")
    print(f"  Avg ganancia:  ${best['avg_win']:+.2f}")
    print(f"  Avg pérdida:   ${best['avg_loss']:+.2f}")

    # Guardar top results en JSON
    out_path = Path(__file__).parent / "logs" / f"optimize_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"top": top_results, "total_tested": len(combos), "valid": len(results)}, f, indent=2)

    # Actualizar optimized_params.json con el mejor
    opt_params = {
        "rsi_trigger":       bp["rsi_trigger"],
        "atr_multiplier_sl": bp["atr_multiplier_sl"],
        "atr_multiplier_tp": bp["atr_multiplier_tp"],
        "adx_min":           bp["adx_min"],
    }
    opt_path = Path(__file__).parent / "strategy" / "optimized_params.json"
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(opt_params, f, indent=2)

    print(f"\n  strategy/optimized_params.json actualizado con los mejores parámetros.")
    print(f"  Reporte completo guardado en: {out_path.name}")

    # Advertencia de overfitting
    print(f"\nADVERTENCIA:")
    print(f"  Estos parámetros son los mejores sobre el PASADO ({days} días).")
    print(f"  Overfitting es un riesgo real — los mejores parámetros históricos")
    print(f"  no garantizan el mismo rendimiento en el futuro.")
    print(f"  Validación recomendada: optimizar en días 365-180, testear en días 180-0.")


def main():
    parser = argparse.ArgumentParser(description="Optimizador de parámetros de la estrategia")
    parser.add_argument("--days",  type=int, default=365, help="Días de historia (default: 365)")
    parser.add_argument("--top",   type=int, default=10,  help="Cuántos resultados mostrar (default: 10)")
    args = parser.parse_args()

    run_optimization(args.days, args.top)


if __name__ == "__main__":
    main()

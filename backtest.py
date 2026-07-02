"""
backtest.py
Backtesting de la estrategia sobre datos históricos reales de Bybit.

Uso:
    python backtest.py                        # 1 año, todos los pares
    python backtest.py --days 180             # últimos 6 meses
    python backtest.py --symbol BTCUSDT       # par específico
    python backtest.py --days 365 --no-cache  # fuerza re-descarga

Descarga velas reales de Bybit (API pública, sin API key).
Guarda los datos en data/ para no re-descargar en cada ejecución.
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from core.indicators import add_indicators

# ── Configuración ──────────────────────────────────────────────────────────────
SYMBOLS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
COMMISSION = 0.001   # 0.1% taker fee spot Bybit
INITIAL_CAPITAL = 5_000.0
RISK_PCT   = 0.01    # 1% por trade (conservador)
MAX_POS_FRAC = 0.25  # máximo 25% del capital en una posición

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_PARAMS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "rsi_trigger": 50,
    "atr_multiplier_sl": 1.5,
    "atr_multiplier_tp": 4.0,
    "adx_min": 25,
}

BYBIT_BASE = "https://api.bybit.com"


# ── Descarga de datos ──────────────────────────────────────────────────────────

def _fetch_klines_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "spot",
        "symbol": symbol,
        "interval": interval,
        "start": start_ms,
        "end": end_ms,
        "limit": 1000,
    }
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            if data.get("retCode") == 0:
                return data["result"]["list"]  # [[ts, o, h, l, c, vol, turnover], ...]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)
    return []


def download_klines(symbol: str, interval: str, days: int, use_cache: bool = True) -> pd.DataFrame:
    cache_file = DATA_DIR / f"{symbol}_{interval}_{days}d.csv"

    if use_cache and cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 12:
            print(f"  {symbol} {interval}m — usando caché ({age_hours:.0f}h)")
            df = pd.read_csv(cache_file)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            return df

    print(f"  {symbol} {interval}m — descargando {days} días...", end="", flush=True)

    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000

    all_rows = []
    cursor   = start_ms
    chunk_ms = 1000 * int(interval) * 60 * 1000  # 1000 velas en ms

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        rows = _fetch_klines_chunk(symbol, interval, cursor, chunk_end)
        if not rows:
            break
        all_rows.extend(rows)
        cursor = chunk_end
        time.sleep(0.05)  # rate limit suave

    print(f" {len(all_rows)} velas")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"]    = pd.to_numeric(df["ts"])
    df["open"]  = df["open"].astype(float)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

    df.to_csv(cache_file, index=False)
    return df


# ── Motor de backtesting ───────────────────────────────────────────────────────

def _add_bias(df_1h: pd.DataFrame, p: dict) -> pd.Series:
    """Sesgo horario: LONG si ema_fast > ema_slow, SHORT si no."""
    df = add_indicators(df_1h.copy())
    bias = pd.Series("NONE", index=df.index)
    bias[df["ema20"] > df["ema50"]] = "LONG"
    bias[df["ema20"] < df["ema50"]] = "SHORT"
    return df.assign(bias=bias)


def run_backtest(symbol: str, df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                 p: dict, capital: float) -> dict:
    # Indicadores en ambos timeframes
    df15 = add_indicators(df_15m.copy())
    df1h = _add_bias(df_1h, p)

    # Alinear sesgo horario al timestamp 15m (merge_asof — último valor horario disponible)
    bias_series = df1h[["datetime", "bias"]].copy()
    bias_series["datetime"] = bias_series["datetime"].dt.floor("15min")
    df15 = pd.merge_asof(
        df15.sort_values("datetime"),
        bias_series.sort_values("datetime").drop_duplicates("datetime"),
        on="datetime",
        direction="backward",
    )
    df15["bias"] = df15["bias"].fillna("NONE")

    trades     = []
    open_trade = None
    peak_cap   = capital
    max_dd     = 0.0

    for i in range(1, len(df15)):
        row  = df15.iloc[i]
        prev = df15.iloc[i - 1]

        # ── Gestión de trade abierto ────────────────────────────────────────
        if open_trade:
            hi, lo = row["high"], row["low"]
            sl = open_trade["sl"]
            tp = open_trade["tp"]

            hit_sl = lo <= sl
            hit_tp = hi >= tp

            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp
                exit_reason = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"

                # fee de salida sobre qty * exit_price
                gross = (exit_price - open_trade["entry"]) * open_trade["qty"]
                fee   = exit_price * open_trade["qty"] * COMMISSION
                pnl   = gross - fee

                capital += pnl
                peak_cap = max(peak_cap, capital)
                dd = (peak_cap - capital) / peak_cap
                max_dd = max(max_dd, dd)

                trades.append({
                    "datetime":    row["datetime"].isoformat(),
                    "symbol":      symbol,
                    "entry":       round(open_trade["entry"], 4),
                    "exit":        round(exit_price, 4),
                    "qty":         round(open_trade["qty"], 6),
                    "pnl":         round(pnl, 4),
                    "exit_reason": exit_reason,
                    "capital":     round(capital, 2),
                })
                open_trade = None

        # ── Generación de señal ─────────────────────────────────────────────
        if open_trade:
            continue

        bias         = row["bias"]
        aligned_long = row["ema20"] > row["ema50"]
        rsi_cross_up = prev["rsi"] <= p["rsi_trigger"] and row["rsi"] > p["rsi_trigger"]

        if bias == "LONG" and aligned_long and rsi_cross_up and row["adx"] >= p.get("adx_min", 0):
            entry = df15.iloc[i + 1]["open"] if i + 1 < len(df15) else row["close"]
            atr_v = row["atr"]
            sl    = entry - atr_v * p["atr_multiplier_sl"]
            tp    = entry + atr_v * p["atr_multiplier_tp"]

            risk_amount  = capital * RISK_PCT
            sl_distance  = entry - sl
            if sl_distance <= 0:
                continue
            qty = risk_amount / sl_distance
            max_qty = (capital * MAX_POS_FRAC) / entry
            qty = min(qty, max_qty)
            if qty <= 0:
                continue

            fee = entry * qty * COMMISSION
            capital -= fee

            open_trade = {"entry": entry, "sl": sl, "tp": tp, "qty": qty}

    # Cerrar posición abierta al último precio
    if open_trade:
        last = df15.iloc[-1]
        exit_price = last["close"]
        gross = (exit_price - open_trade["entry"]) * open_trade["qty"]
        fee   = exit_price * open_trade["qty"] * COMMISSION
        pnl   = gross - fee
        capital += pnl
        trades.append({
            "datetime":    last["datetime"].isoformat(),
            "symbol":      symbol,
            "entry":       round(open_trade["entry"], 4),
            "exit":        round(exit_price, 4),
            "qty":         round(open_trade["qty"], 6),
            "pnl":         round(pnl, 4),
            "exit_reason": "MARKET_CLOSE",
            "capital":     round(capital, 2),
        })

    return {"trades": trades, "final_capital": capital, "max_drawdown_pct": round(max_dd * 100, 2)}


# ── Métricas ───────────────────────────────────────────────────────────────────

def compute_metrics(results: list, initial_capital: float) -> dict:
    all_trades = []
    for r in results:
        all_trades.extend(r["trades"])

    if not all_trades:
        return {"error": "Sin trades — la estrategia no generó ninguna señal."}

    all_trades.sort(key=lambda t: t["datetime"])
    pnls = [t["pnl"] for t in all_trades]

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl    = sum(pnls)
    win_rate     = len(wins) / len(pnls) * 100
    avg_win      = sum(wins) / len(wins) if wins else 0
    avg_loss     = sum(losses) / len(losses) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")

    # Sharpe aproximado (retornos por trade / std)
    mean_pnl = total_pnl / len(pnls)
    std_pnl  = pd.Series(pnls).std()
    sharpe   = (mean_pnl / std_pnl * math.sqrt(len(pnls))) if std_pnl > 0 else 0

    max_dd = max(r["max_drawdown_pct"] for r in results)
    final_cap = initial_capital + total_pnl
    roi = (final_cap - initial_capital) / initial_capital * 100

    return {
        "total_trades":   len(all_trades),
        "win_rate":       round(win_rate, 1),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(profit_factor, 2),
        "sharpe":         round(sharpe, 2),
        "total_pnl":      round(total_pnl, 2),
        "roi_pct":        round(roi, 2),
        "max_drawdown":   round(max_dd, 2),
        "initial_capital": initial_capital,
        "final_capital":  round(final_cap, 2),
        "trades":         all_trades,
    }


def print_report(metrics: dict, days: int, params: dict):
    if "error" in metrics:
        print(f"\n[ERROR] {metrics['error']}")
        return

    print("\n" + "=" * 60)
    print(f"BACKTEST — {days} días | {datetime.now().strftime('%Y-%m-%d')}")
    print(f"Estrategia: EMA{params['ema_fast']}/{params['ema_slow']} + RSI{params['rsi_trigger']} | SL {params['atr_multiplier_sl']}x ATR | TP {params['atr_multiplier_tp']}x ATR")
    print("=" * 60)
    print(f"  Capital inicial:  ${metrics['initial_capital']:>10,.2f}")
    print(f"  Capital final:    ${metrics['final_capital']:>10,.2f}")
    print(f"  P&L total:        ${metrics['total_pnl']:>+10,.2f}  ({metrics['roi_pct']:+.1f}%)")
    print(f"  Max Drawdown:     {metrics['max_drawdown']:>9.1f}%")
    print()
    print(f"  Trades totales:   {metrics['total_trades']}")
    print(f"  Win rate:         {metrics['win_rate']:.1f}%")
    print(f"  Avg ganancia:     ${metrics['avg_win']:>+8.2f}")
    print(f"  Avg pérdida:      ${metrics['avg_loss']:>+8.2f}")
    print(f"  Profit factor:    {metrics['profit_factor']:.2f}  (>1.5 = aceptable, >2 = bueno)")
    print(f"  Sharpe:           {metrics['sharpe']:.2f}  (>1.0 = aceptable, >2 = bueno)")
    print("=" * 60)

    # Desglose por símbolo
    by_symbol = {}
    for t in metrics["trades"]:
        s = t["symbol"]
        by_symbol.setdefault(s, []).append(t["pnl"])

    print("\nDesglose por par:")
    for sym, pnls in sorted(by_symbol.items()):
        w = sum(1 for p in pnls if p > 0)
        print(f"  {sym:<10} {len(pnls):>3} trades | WR {w/len(pnls)*100:.0f}% | P&L ${sum(pnls):>+.2f}")

    # Veredicto
    print("\nVEREDICTO:")
    pf = metrics["profit_factor"]
    sh = metrics["sharpe"]
    wr = metrics["win_rate"]
    n  = metrics["total_trades"]

    if n < 30:
        verdict = f"INSUFICIENTE — solo {n} trades. Necesitas ≥30 para conclusiones mínimas, ≥100 para confianza."
    elif pf < 1.0:
        verdict = "NEGATIVO — el sistema pierde dinero. No operar en real."
    elif pf < 1.5 or sh < 0.5:
        verdict = "MARGINAL — rentable pero frágil. Comisiones reales o slippage lo rompen."
    elif pf >= 1.5 and sh >= 1.0 and wr >= 40:
        verdict = "VIABLE — métricas aceptables para considerar cuenta real con capital mínimo."
    else:
        verdict = "REVISAR — métricas mixtas. Ver desglose detallado."

    print(f"  {verdict}")
    print()

    # Guardar JSON
    out_path = Path(__file__).parent / "logs" / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"  Resultados guardados en: {out_path.name}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest de la estrategia trading bot")
    parser.add_argument("--days",    type=int,   default=365,    help="Días de historia (default: 365)")
    parser.add_argument("--symbol",  type=str,   default=None,   help="Par específico (default: todos)")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL, help="Capital inicial USDT")
    parser.add_argument("--no-cache", action="store_true", help="Forzar re-descarga de datos")
    args = parser.parse_args()

    symbols   = [args.symbol] if args.symbol else SYMBOLS
    use_cache = not args.no_cache
    params    = DEFAULT_PARAMS.copy()

    # Cargar parámetros optimizados si existen
    opt_path = Path(__file__).parent / "strategy" / "optimized_params.json"
    if opt_path.exists():
        with open(opt_path) as f:
            opt = json.load(f)
        params.update(opt)
        print(f"Parámetros cargados desde optimized_params.json: {params}")
    else:
        print(f"Usando parámetros por defecto: {params}")

    print(f"\nDescargando datos históricos ({args.days} días)...")
    results = []

    for sym in symbols:
        try:
            df_15m = download_klines(sym, "15",  args.days, use_cache)
            df_1h  = download_klines(sym, "60",  args.days, use_cache)

            if df_15m.empty or df_1h.empty:
                print(f"  {sym} — sin datos, saltando")
                continue

            r = run_backtest(sym, df_15m, df_1h, params, args.capital / len(symbols))
            r["symbol"] = sym
            results.append(r)
            t = r["trades"]
            wins = sum(1 for x in t if x["pnl"] > 0)
            print(f"  {sym} — {len(t)} trades | {wins}/{len(t)} wins | P&L ${sum(x['pnl'] for x in t):+.2f}")

        except Exception as e:
            print(f"  {sym} — ERROR: {e}")

    if not results:
        print("\nNo se obtuvieron resultados.")
        return

    metrics = compute_metrics(results, args.capital)
    print_report(metrics, args.days, params)


if __name__ == "__main__":
    main()

"""
optimize_core.py
Paso 1: Grid search de parametros del Core (Donchian + ATR) sobre 4 activos base
Paso 2: Portfolio final con mejores parametros + universo expandido (14 activos)
         + Satelite crypto 20%

Universo expandido:
  Metales:     GLD (oro), SLV (plata), GDX (mineras oro)
  Energia:     USO (petroleo), XLE (energia amplia)
  Commodities: DBC (cestas), PDBC (sin roll cost)
  Bonos:       TLT (largo plazo), IEF (medio plazo), TIP (inflacion)
  Forex ETF:   FXE (EUR/USD), FXY (JPY/USD), FXB (GBP/USD)
  Extra:       XME (metales industriales)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import itertools, json, math, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ── Capital ────────────────────────────────────────────────────────────────────
TOTAL_CAPITAL  = 10_000.0
CORE_PCT       = 0.80
SATELLITE_PCT  = 0.20
YEARS          = 4

# ── Universo ───────────────────────────────────────────────────────────────────
BASE_SYMBOLS     = ["GLD", "DBC", "GDX", "TLT"]        # para optimizacion (rapido)
EXPANDED_SYMBOLS = [
    "GLD", "SLV", "GDX",           # metales
    "USO", "XLE",                   # energia
    "DBC", "PDBC",                  # commodities broad
    "TLT", "IEF", "TIP",            # bonos
    "FXE", "FXY", "FXB",            # forex
    "XME",                          # metales industriales
]
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# ── Grid de parametros ─────────────────────────────────────────────────────────
PARAM_GRID = {
    "don_entry": [10, 15, 20, 25, 30, 40, 55],
    "don_exit":  [5, 7, 10, 15, 20],
    "atr_mult":  [1.5, 2.0, 2.5, 3.0],
}
MIN_TRADES   = 15
RISK_PCT     = 0.02
C_COMMISSION = 0.0005
B_COMMISSION = 0.001

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
#  DATOS
# ══════════════════════════════════════════════════════════════

def load_yf(symbol, years):
    cache = DATA_DIR / f"{symbol}_1D_{years}y_yf.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 43200:
        df = pd.read_csv(cache, parse_dates=["date"])
        return df
    df = yf.download(symbol, period=f"{years}y", interval="1d",
                     progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df = df[["Open","High","Low","Close"]].copy()
    df.columns = ["open","high","low","close"]
    df.index.name = "date"
    df = df.dropna().reset_index()
    df.to_csv(cache, index=False)
    return df


def load_bybit(symbol, years):
    cache = DATA_DIR / f"{symbol}_1D_{years*365}d.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 43200:
        df = pd.read_csv(cache)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        return df
    print(f"  Descargando {symbol}...", end="", flush=True)
    end_ms, rows, cursor = int(datetime.now(timezone.utc).timestamp()*1000), [], 0
    start_ms = end_ms - years*365*86_400_000
    cursor = start_ms
    while cursor < end_ms:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={
            "category":"spot","symbol":symbol,"interval":"D",
            "start":cursor,"end":min(cursor+200*86_400_000,end_ms),"limit":200,
        }, timeout=15)
        d = r.json()
        if d.get("retCode")==0:
            rows.extend(d["result"]["list"])
        cursor += 200*86_400_000
        time.sleep(0.1)
    print(f" {len(rows)} velas")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume","turnover"])
    df["ts"] = pd.to_numeric(df["ts"])
    for c in ["open","high","low","close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.to_csv(cache, index=False)
    return df


# ══════════════════════════════════════════════════════════════
#  MOTOR BACKTEST CORE (Donchian)
# ══════════════════════════════════════════════════════════════

def compute_atr(df, period=20):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    return pd.concat([hl,hc,lc],axis=1).max(axis=1).ewm(span=period,adjust=False).mean()


def backtest_donchian(df, don_entry, don_exit, atr_mult, capital):
    df = df.copy()
    df["don_high"] = df["close"].shift(1).rolling(don_entry).max()
    df["don_low"]  = df["close"].shift(1).rolling(don_exit).min()
    df["atr"]      = compute_atr(df, 20)
    df = df.dropna().reset_index(drop=True)

    trades, open_trade = [], None
    cap, peak, max_dd = capital, capital, 0.0
    equity = {}

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]
        date = str(row["date"])[:10] if "date" in row else str(row.name)[:10]

        float_val = cap + ((row["close"] - open_trade["entry"]) * open_trade["qty"]
                           if open_trade else 0)
        equity[date] = float_val
        peak   = max(peak, float_val)
        max_dd = max(max_dd, (peak - float_val) / peak)

        if open_trade:
            if row["low"] <= open_trade["sl"] or row["close"] < row["don_low"]:
                ep  = open_trade["sl"] if row["low"] <= open_trade["sl"] else row["open"]
                pnl = (ep - open_trade["entry"]) * open_trade["qty"]
                pnl -= ep * open_trade["qty"] * C_COMMISSION
                cap += pnl
                trades.append(pnl)
                open_trade = None
            else:
                open_trade["sl"] = max(open_trade["sl"], row["don_low"])
            continue

        if prev["close"] <= prev["don_high"] and row["close"] > row["don_high"]:
            entry = row["close"]
            sl    = entry - atr_mult * row["atr"]
            dist  = entry - sl
            if dist <= 0:
                continue
            qty = min((cap * RISK_PCT) / dist, (cap * 0.95) / entry)
            if qty <= 0:
                continue
            cap -= entry * qty * C_COMMISSION
            open_trade = {"entry": entry, "sl": sl, "qty": qty}

    if open_trade:
        ep  = df.iloc[-1]["close"]
        pnl = (ep - open_trade["entry"]) * open_trade["qty"]
        pnl -= ep * open_trade["qty"] * C_COMMISSION
        cap += pnl
        trades.append(pnl)

    wins   = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    pf     = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else (99 if wins else 0)
    mean   = sum(trades)/len(trades) if trades else 0
    std    = pd.Series(trades).std() if len(trades) > 1 else 1
    sharpe = mean/std*math.sqrt(len(trades)) if std > 0 else 0
    eq_s   = pd.Series(equity)
    ret    = eq_s.pct_change().dropna()
    sharpe_a = ret.mean()/ret.std()*math.sqrt(252) if ret.std()>0 else 0

    return {
        "trades": len(trades), "pf": round(pf,3), "sharpe": round(sharpe,2),
        "sharpe_annual": round(sharpe_a,2), "roi": round((cap-capital)/capital*100,2),
        "max_dd": round(max_dd*100,2), "final": round(cap,2),
        "equity": equity,
    }


# ══════════════════════════════════════════════════════════════
#  PASO 1 — OPTIMIZACION
# ══════════════════════════════════════════════════════════════

def optimize():
    print("\n" + "="*65)
    print("PASO 1 — OPTIMIZACION DE PARAMETROS DEL CORE")
    print(f"Grid: {len(PARAM_GRID['don_entry'])} x {len(PARAM_GRID['don_exit'])} x {len(PARAM_GRID['atr_mult'])} = "
          f"{len(PARAM_GRID['don_entry'])*len(PARAM_GRID['don_exit'])*len(PARAM_GRID['atr_mult'])} combinaciones")
    print(f"Activos de prueba: {', '.join(BASE_SYMBOLS)}")
    print("="*65)

    print("\nCargando datos base...")
    datasets = {}
    cap_per  = (TOTAL_CAPITAL * CORE_PCT) / len(BASE_SYMBOLS)
    for sym in BASE_SYMBOLS:
        df = load_yf(sym, YEARS)
        if not df.empty:
            datasets[sym] = df
            print(f"  {sym}: {len(df)} dias")

    combos  = list(itertools.product(
        PARAM_GRID["don_entry"], PARAM_GRID["don_exit"], PARAM_GRID["atr_mult"]
    ))
    results = []

    print(f"\nProbando {len(combos)} combinaciones...", flush=True)
    for i, (de, dx, am) in enumerate(combos):
        if dx >= de:        # don_exit debe ser menor que don_entry
            continue
        if (i+1) % 30 == 0:
            print(f"  [{i+1}/{len(combos)}]", flush=True)

        agg = {"trades":0, "pf_sum":0, "sharpe_sum":0, "roi_sum":0, "dd_max":0, "valid":0}
        for sym, df in datasets.items():
            r = backtest_donchian(df, de, dx, am, cap_per)
            if r["trades"] < MIN_TRADES:
                continue
            agg["trades"]    += r["trades"]
            agg["pf_sum"]    += r["pf"]
            agg["sharpe_sum"]+= r["sharpe_annual"]
            agg["roi_sum"]   += r["roi"]
            agg["dd_max"]     = max(agg["dd_max"], r["max_dd"])
            agg["valid"]     += 1

        if agg["valid"] < 2:
            continue

        avg_pf     = agg["pf_sum"]    / agg["valid"]
        avg_sharpe = agg["sharpe_sum"]/ agg["valid"]
        avg_roi    = agg["roi_sum"]   / agg["valid"]
        results.append({
            "don_entry": de, "don_exit": dx, "atr_mult": am,
            "pf": round(avg_pf,3), "sharpe": round(avg_sharpe,2),
            "roi": round(avg_roi,2), "max_dd": round(agg["dd_max"],2),
            "trades": agg["trades"],
        })

    results.sort(key=lambda x: (x["sharpe"], x["pf"]), reverse=True)

    print(f"\nTOP 10 combinaciones (ordenadas por Sharpe):")
    print(f"  {'#':>2} {'Entry':>6} {'Exit':>5} {'ATR':>5} {'PF':>6} {'Sharpe':>7} {'ROI%':>6} {'DD%':>6} {'Trades':>7}")
    print("  " + "-"*58)
    for rank, r in enumerate(results[:10], 1):
        mark = " <<" if rank == 1 else ""
        print(f"  {rank:>2} {r['don_entry']:>6} {r['don_exit']:>5} {r['atr_mult']:>5} "
              f"{r['pf']:>6.2f} {r['sharpe']:>7.2f} {r['roi']:>+6.1f} "
              f"{r['max_dd']:>6.1f} {r['trades']:>7}{mark}")

    best = results[0]
    print(f"\n  MEJOR: Donchian {best['don_entry']}/{best['don_exit']} | ATR {best['atr_mult']}x SL")
    print(f"         PF {best['pf']} | Sharpe {best['sharpe']} | ROI {best['roi']:+.1f}% | MaxDD {best['max_dd']:.1f}%")
    return best


# ══════════════════════════════════════════════════════════════
#  PASO 2 — PORTFOLIO FINAL
# ══════════════════════════════════════════════════════════════

def run_full_portfolio(best_params):
    de = best_params["don_entry"]
    dx = best_params["don_exit"]
    am = best_params["atr_mult"]

    core_cap = TOTAL_CAPITAL * CORE_PCT
    sat_cap  = TOTAL_CAPITAL * SATELLITE_PCT
    cap_per  = core_cap / len(EXPANDED_SYMBOLS)

    print("\n" + "="*65)
    print("PASO 2 — PORTFOLIO FINAL CON UNIVERSO EXPANDIDO")
    print(f"  Parametros Core: Donchian {de}/{dx} | ATR {am}x SL")
    print(f"  Activos Core ({len(EXPANDED_SYMBOLS)}): {', '.join(EXPANDED_SYMBOLS)}")
    print(f"  Satelite: {', '.join(CRYPTO_SYMBOLS)}")
    print("="*65)

    # ── Core ──────────────────────────────────────────────────
    print("\n[Core — Trend Following expandido]")
    core_equity, core_trades_all = {}, []
    skipped = []

    for sym in EXPANDED_SYMBOLS:
        df = load_yf(sym, YEARS)
        if df.empty:
            skipped.append(sym)
            continue
        r = backtest_donchian(df, de, dx, am, cap_per)
        core_equity[sym] = pd.Series(r["equity"])
        wins = sum(1 for p in [r] if r["pf"] > 1)
        n    = r["trades"]
        print(f"  {sym:<6}: {n:>3} trades | PF {r['pf']:.2f} | ROI {r['roi']:>+.1f}% | MaxDD {r['max_dd']:.1f}%")

    if skipped:
        print(f"  Sin datos: {', '.join(skipped)}")

    core_eq = pd.DataFrame(core_equity).ffill().sum(axis=1)
    core_eq.index = pd.to_datetime(core_eq.index)

    # ── Satelite ──────────────────────────────────────────────
    print("\n[Satelite — Crypto EMA-200]")
    sat_equity = {}
    for sym in CRYPTO_SYMBOLS:
        df = load_bybit(sym, YEARS)
        if df.empty:
            continue
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
        df = df.dropna().reset_index(drop=True)

        cap_s, open_t, equity = sat_cap/len(CRYPTO_SYMBOLS), None, {}
        for i in range(1, len(df)):
            row, prev = df.iloc[i], df.iloc[i-1]
            date = str(row["datetime"])[:10]
            float_val = cap_s + ((row["close"]-open_t["entry"])*open_t["qty"] if open_t else 0)
            equity[date] = float_val

            if open_t:
                if row["close"] < row["ema200"]:
                    ep  = row["open"]
                    pnl = (ep-open_t["entry"])*open_t["qty"] - ep*open_t["qty"]*B_COMMISSION
                    cap_s += pnl
                    open_t = None
                continue
            if prev["close"] < prev["ema200"] and row["close"] > row["ema200"]:
                entry = row["close"]
                qty   = (cap_s*0.95)/entry
                cap_s -= entry*qty*B_COMMISSION
                open_t = {"entry": entry, "qty": qty}

        if open_t:
            ep  = df.iloc[-1]["close"]
            pnl = (ep-open_t["entry"])*open_t["qty"] - ep*open_t["qty"]*B_COMMISSION
            cap_s += pnl

        sat_equity[sym] = pd.Series(equity)
        print(f"  {sym}: final ${cap_s:,.2f}")

    sat_eq = pd.DataFrame(sat_equity).ffill().sum(axis=1)
    sat_eq.index = pd.to_datetime(sat_eq.index)

    # ── Combinado ─────────────────────────────────────────────
    combined = pd.concat([core_eq, sat_eq], axis=1).ffill().sum(axis=1)

    # ── Benchmark SPY ─────────────────────────────────────────
    spy_df = yf.download("SPY", period=f"{YEARS}y", interval="1d",
                          progress=False, auto_adjust=True)
    spy_closes = spy_df["Close"].dropna().squeeze()
    spy_closes.index = pd.to_datetime(spy_closes.index)
    spy_eq = spy_closes * (TOTAL_CAPITAL / float(spy_closes.iloc[0]))

    # ── Metricas ──────────────────────────────────────────────
    def metrics(eq, initial, label):
        eq = eq.dropna()
        if eq.empty:
            return {}
        ret     = eq.pct_change().dropna()
        total_r = (eq.iloc[-1] - initial) / initial * 100
        peak    = eq.cummax()
        max_dd  = ((eq - peak) / peak).min() * 100
        sharpe  = ret.mean()/ret.std()*math.sqrt(252) if ret.std()>0 else 0
        return {"label": label, "initial": round(initial,2),
                "final": round(eq.iloc[-1],2), "roi": round(total_r,1),
                "max_dd": round(max_dd,1), "sharpe": round(sharpe,2)}

    common = combined.index.intersection(spy_eq.index)
    m_port = metrics(combined.loc[common], TOTAL_CAPITAL, "Portfolio")
    m_spy  = metrics(spy_eq.loc[common],  TOTAL_CAPITAL, "SPY B&H")
    m_core = metrics(core_eq,  core_cap, f"Core ({len(EXPANDED_SYMBOLS)} activos)")
    m_sat  = metrics(sat_eq,   sat_cap,  "Satelite (BTC+ETH)")

    # ── Reporte ───────────────────────────────────────────────
    print("\n" + "="*65)
    print("RESULTADOS FINALES")
    print("="*65)
    print(f"\n  {'':32} {'ROI':>7} {'MaxDD':>7} {'Sharpe':>8} {'Final':>12}")
    print("  " + "-"*68)
    for m in [m_port, m_spy, m_core, m_sat]:
        mark = " <<<" if m["label"]=="Portfolio" else ""
        print(f"  {m['label']:<32} {m['roi']:>+7.1f}% {m['max_dd']:>6.1f}% "
              f"{m['sharpe']:>8.2f}  ${m['final']:>10,.2f}{mark}")

    # Comparacion vs portfolio anterior
    prev_roi   = 12.2
    prev_dd    = -50.3
    prev_sharpe= 0.27

    print(f"\n  MEJORA vs portfolio anterior (4 activos, params originales):")
    print(f"    ROI:    {prev_roi:>+.1f}%  ->  {m_port['roi']:>+.1f}%   ({m_port['roi']-prev_roi:>+.1f}pp)")
    print(f"    MaxDD: {prev_dd:.1f}%  ->  {m_port['max_dd']:.1f}%   ({m_port['max_dd']-prev_dd:>+.1f}pp)")
    print(f"    Sharpe: {prev_sharpe:.2f}   ->  {m_port['sharpe']:.2f}    ({m_port['sharpe']-prev_sharpe:>+.2f})")

    # Veredicto
    pf_note = ""
    if m_port["sharpe"] >= 1.0 and m_port["roi"] > m_spy["roi"] and m_port["max_dd"] > -40:
        verdict = "VIABLE — supera al benchmark con Sharpe aceptable."
    elif m_port["sharpe"] >= 0.5 and m_port["roi"] > 0:
        verdict = "MARGINAL — rentable, mejoro vs version anterior, aun no ideal."
    else:
        verdict = "INSUFICIENTE — requiere mas ajustes."

    print(f"\n  VEREDICTO: {verdict}")

    # Guardar
    out = Path(__file__).parent/"logs"/f"portfolio_final_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out,"w",encoding="utf-8") as f:
        json.dump({"best_params": best_params,
                   "metrics": {"portfolio":m_port,"core":m_core,"satellite":m_sat,"spy":m_spy}},
                  f, indent=2, default=str)
    print(f"\n  Guardado: {out.name}")
    print("="*65)


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    best = optimize()
    run_full_portfolio(best)

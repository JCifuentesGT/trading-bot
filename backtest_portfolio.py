"""
PORTFOLIO COMBINADO
  Core (80%):     Estrategia C — Trend Following Donchian 20/10
                  Mercado: GLD, DBC, GDX, TLT (ETFs commodities/bonos)
  Satelite (20%): Estrategia B — Crypto EMA-200 diario
                  Mercado: BTC, ETH

Metricas reportadas:
  - Cada componente por separado
  - Portfolio combinado
  - Comparacion vs Buy & Hold SPY (benchmark)
  - Drawdown real (incluyendo posiciones abiertas)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import math
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf
import requests

TOTAL_CAPITAL   = 10_000.0
CORE_PCT        = 0.80      # 80% Estrategia C
SATELLITE_PCT   = 0.20      # 20% Estrategia B

# --- Estrategia C params ---
C_SYMBOLS       = ["GLD", "DBC", "GDX", "TLT"]
C_YEARS         = 4         # mismo periodo que B para comparacion justa
C_DONCHIAN_IN   = 20
C_DONCHIAN_OUT  = 10
C_ATR_PERIOD    = 20
C_ATR_SL_MULT   = 2.0
C_RISK_PCT      = 0.02
C_COMMISSION    = 0.0005

# --- Estrategia B params ---
B_SYMBOLS       = ["BTCUSDT", "ETHUSDT"]
B_YEARS         = 4
B_COMMISSION    = 0.001

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════════════════════════

def compute_atr(df, period=20):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).ewm(span=period, adjust=False).mean()


def compute_rsi2(series):
    d = series.diff()
    g = d.clip(lower=0).ewm(span=2, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=2, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def portfolio_metrics(equity_curve: pd.Series, initial: float, label: str):
    """Calcula metricas sobre una curva de equity diaria."""
    if equity_curve.empty:
        return {}
    returns   = equity_curve.pct_change().dropna()
    total_ret = (equity_curve.iloc[-1] - initial) / initial * 100
    peak      = equity_curve.cummax()
    drawdown  = (equity_curve - peak) / peak * 100
    max_dd    = drawdown.min()
    sharpe    = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
    return {
        "label":         label,
        "initial":       round(initial, 2),
        "final":         round(equity_curve.iloc[-1], 2),
        "roi_pct":       round(total_ret, 1),
        "max_drawdown":  round(max_dd, 1),
        "sharpe_annual": round(sharpe, 2),
    }


# ══════════════════════════════════════════════════════════════
#  ESTRATEGIA C — TREND FOLLOWING (DONCHIAN)
# ══════════════════════════════════════════════════════════════

def load_yf(symbol, years):
    df = yf.download(symbol, period=f"{years}y", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df = df[["Open","High","Low","Close"]].copy()
    df.columns = ["open","high","low","close"]
    df.index.name = "date"
    return df.dropna().reset_index()


def run_core(capital):
    cap_per    = capital / len(C_SYMBOLS)
    all_trades = []
    daily_caps = {}   # symbol -> Series(date -> capital)

    for sym in C_SYMBOLS:
        df = load_yf(sym, C_YEARS)
        if df.empty:
            print(f"  [C] {sym} — sin datos")
            continue

        df["don_high"] = df["close"].shift(1).rolling(C_DONCHIAN_IN).max()
        df["don_low"]  = df["close"].shift(1).rolling(C_DONCHIAN_OUT).min()
        df["atr"]      = compute_atr(df, C_ATR_PERIOD)
        df = df.dropna().reset_index(drop=True)

        cap        = cap_per
        open_trade = None
        equity     = {}

        for i in range(1, len(df)):
            row  = df.iloc[i]
            prev = df.iloc[i-1]
            date = str(row["date"])[:10]

            floating_val = cap
            if open_trade:
                floating_val = cap + (row["close"] - open_trade["entry"]) * open_trade["qty"]

            equity[date] = floating_val

            if open_trade:
                hit_sl   = row["low"] <= open_trade["sl"]
                don_exit = row["close"] < row["don_low"]
                if hit_sl or don_exit:
                    ep  = open_trade["sl"] if hit_sl else row["open"]
                    pnl = (ep - open_trade["entry"]) * open_trade["qty"]
                    pnl -= ep * open_trade["qty"] * C_COMMISSION
                    cap += pnl
                    reason = "STOP_LOSS" if hit_sl else "DONCHIAN_EXIT"
                    all_trades.append({"date": date, "symbol": sym, "strategy": "C",
                                       "entry": round(open_trade["entry"],4), "exit": round(ep,4),
                                       "pnl": round(pnl,4), "reason": reason})
                    # trailing SL
                    if not hit_sl:
                        open_trade = None
                    else:
                        open_trade = None
                else:
                    open_trade["sl"] = max(open_trade["sl"], row["don_low"])
                continue

            breakout = prev["close"] <= prev["don_high"] and row["close"] > row["don_high"]
            if breakout:
                entry = row["close"]
                sl    = entry - C_ATR_SL_MULT * row["atr"]
                dist  = entry - sl
                if dist <= 0:
                    continue
                qty = min((cap * C_RISK_PCT) / dist, (cap * 0.95) / entry)
                if qty <= 0:
                    continue
                cap -= entry * qty * C_COMMISSION
                open_trade = {"entry": entry, "sl": sl, "qty": qty}

        # Cierre al final
        if open_trade:
            ep  = df.iloc[-1]["close"]
            pnl = (ep - open_trade["entry"]) * open_trade["qty"]
            pnl -= ep * open_trade["qty"] * C_COMMISSION
            cap += pnl
            all_trades.append({"date": str(df.iloc[-1]["date"])[:10], "symbol": sym,
                                "strategy": "C", "entry": round(open_trade["entry"],4),
                                "exit": round(ep,4), "pnl": round(pnl,4), "reason": "END"})

        daily_caps[sym] = pd.Series(equity, name=sym)
        wins = sum(1 for t in all_trades if t["symbol"]==sym and t["pnl"]>0)
        n    = sum(1 for t in all_trades if t["symbol"]==sym)
        print(f"  [C] {sym}: {n} trades | {wins}/{n} wins | final ${cap:,.2f}")

    # Equity curve del core: suma diaria de todos los simbolos
    if not daily_caps:
        return pd.Series(dtype=float), [], capital
    eq_df   = pd.DataFrame(daily_caps).ffill()
    core_eq = eq_df.sum(axis=1)
    final   = core_eq.iloc[-1]
    return core_eq, all_trades, final


# ══════════════════════════════════════════════════════════════
#  ESTRATEGIA B — CRYPTO EMA-200
# ══════════════════════════════════════════════════════════════

def fetch_daily_bybit(symbol, days):
    cache = DATA_DIR / f"{symbol}_1D_{days}d.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 43200:
        df = pd.read_csv(cache)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        return df
    print(f"  [B] {symbol} — descargando...", end="", flush=True)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows, cursor = [], start_ms
    while cursor < end_ms:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={
            "category":"spot","symbol":symbol,"interval":"D",
            "start":cursor,"end":min(cursor+200*86_400_000,end_ms),"limit":200,
        }, timeout=15)
        d = r.json()
        if d.get("retCode") == 0:
            rows.extend(d["result"]["list"])
        cursor += 200 * 86_400_000
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


def run_satellite(capital):
    cap_per    = capital / len(B_SYMBOLS)
    all_trades = []
    daily_caps = {}

    for sym in B_SYMBOLS:
        df = fetch_daily_bybit(sym, B_YEARS * 365)
        if df.empty:
            continue
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
        df = df.dropna().reset_index(drop=True)

        cap        = cap_per
        open_trade = None
        equity     = {}

        for i in range(1, len(df)):
            row  = df.iloc[i]
            prev = df.iloc[i-1]
            date = str(row["datetime"])[:10]

            floating_val = cap
            if open_trade:
                floating_val = cap + (row["close"] - open_trade["entry"]) * open_trade["qty"]
            equity[date] = floating_val

            if open_trade:
                if row["close"] < row["ema200"]:
                    ep  = row["open"]
                    pnl = (ep - open_trade["entry"]) * open_trade["qty"]
                    pnl -= ep * open_trade["qty"] * B_COMMISSION
                    cap += pnl
                    all_trades.append({"date": date, "symbol": sym, "strategy": "B",
                                       "entry": round(open_trade["entry"],2), "exit": round(ep,2),
                                       "pnl": round(pnl,4), "reason": "EMA_CROSS_DOWN"})
                    open_trade = None
                continue

            if prev["close"] < prev["ema200"] and row["close"] > row["ema200"]:
                entry = row["close"]
                qty   = (cap * 0.95) / entry
                cap  -= entry * qty * B_COMMISSION
                open_trade = {"entry": entry, "qty": qty}

        if open_trade:
            ep  = df.iloc[-1]["close"]
            pnl = (ep - open_trade["entry"]) * open_trade["qty"]
            pnl -= ep * open_trade["qty"] * B_COMMISSION
            cap += pnl
            all_trades.append({"date": str(df.iloc[-1]["datetime"])[:10], "symbol": sym,
                                "strategy": "B", "entry": round(open_trade["entry"],2),
                                "exit": round(ep,2), "pnl": round(pnl,4), "reason": "END"})

        daily_caps[sym] = pd.Series(equity, name=sym)
        wins = sum(1 for t in all_trades if t["symbol"]==sym and t["pnl"]>0)
        n    = sum(1 for t in all_trades if t["symbol"]==sym)
        print(f"  [B] {sym}: {n} trades | {wins}/{n} wins | final ${cap:,.2f}")

    if not daily_caps:
        return pd.Series(dtype=float), [], capital
    eq_df   = pd.DataFrame(daily_caps).ffill()
    sat_eq  = eq_df.sum(axis=1)
    return sat_eq, all_trades, sat_eq.iloc[-1]


# ══════════════════════════════════════════════════════════════
#  BENCHMARK — SPY Buy & Hold
# ══════════════════════════════════════════════════════════════

def buy_and_hold_spy(capital, years):
    df = yf.download("SPY", period=f"{years}y", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.Series(dtype=float)
    closes = df["Close"].dropna().squeeze()   # asegura Series, no DataFrame
    factor = capital / float(closes.iloc[0])
    eq = closes * factor
    eq.index = pd.to_datetime(eq.index)
    eq.name = "SPY"
    return eq


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    core_cap = TOTAL_CAPITAL * CORE_PCT
    sat_cap  = TOTAL_CAPITAL * SATELLITE_PCT

    print("=" * 65)
    print("PORTFOLIO COMBINADO")
    print(f"  Core (80% = ${core_cap:,.0f}): Trend Following — GLD, DBC, GDX, TLT")
    print(f"  Satelite (20% = ${sat_cap:,.0f}): Crypto EMA-200 — BTC, ETH")
    print(f"  Periodo: {C_YEARS} anos | Capital total: ${TOTAL_CAPITAL:,.0f}")
    print("=" * 65)

    print("\n[Corriendo Core — Trend Following]")
    core_eq, core_trades, core_final = run_core(core_cap)

    print("\n[Corriendo Satelite — Crypto EMA-200]")
    sat_eq, sat_trades, sat_final = run_satellite(sat_cap)

    # Combinar equity curves
    combined = pd.concat([core_eq, sat_eq], axis=1).ffill().sum(axis=1)
    combined.index = pd.to_datetime(combined.index)

    # Benchmark
    spy_eq = buy_and_hold_spy(TOTAL_CAPITAL, C_YEARS)
    spy_eq.index = pd.to_datetime(spy_eq.index)

    # Alinear indices
    common_idx = combined.index.intersection(spy_eq.index)
    if len(common_idx) > 0:
        combined_al = combined.loc[common_idx]
        spy_al      = spy_eq.loc[common_idx]
    else:
        combined_al = combined
        spy_al      = spy_eq

    # Metricas
    m_core  = portfolio_metrics(core_eq,       core_cap,        "Core (C)")
    m_sat   = portfolio_metrics(sat_eq,        sat_cap,         "Satelite (B)")
    m_port  = portfolio_metrics(combined_al,   TOTAL_CAPITAL,   "Portfolio")
    m_spy   = portfolio_metrics(spy_al,        TOTAL_CAPITAL,   "SPY B&H")

    all_trades = core_trades + sat_trades
    pnls  = [t["pnl"] for t in all_trades]
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]
    pf    = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else float("inf")

    # Reporte
    print()
    print("=" * 65)
    print("RESULTADOS")
    print("=" * 65)

    for m in [m_core, m_sat, m_port, m_spy]:
        sep = " <<< PORTFOLIO" if m["label"] == "Portfolio" else ""
        print(f"\n  {m['label']}{sep}")
        print(f"    Capital inicial: ${m['initial']:>10,.2f}")
        print(f"    Capital final:   ${m['final']:>10,.2f}")
        print(f"    ROI:             {m['roi_pct']:>+.1f}%")
        print(f"    Max Drawdown:    {m['max_drawdown']:.1f}%")
        print(f"    Sharpe anual:    {m['sharpe_annual']:.2f}")

    print()
    print("=" * 65)
    print("RESUMEN COMPARATIVO")
    print("=" * 65)
    print(f"  {'':30} {'ROI':>8} {'MaxDD':>8} {'Sharpe':>8}")
    print(f"  {'-'*54}")
    for m in [m_port, m_spy, m_core, m_sat]:
        print(f"  {m['label']:<30} {m['roi_pct']:>+7.1f}% {m['max_drawdown']:>7.1f}% {m['sharpe_annual']:>8.2f}")

    print()
    print(f"  Trades totales: {len(all_trades)}  |  Win rate: {len(wins)/len(pnls)*100:.1f}%  |  Profit Factor: {pf:.2f}")

    # Veredicto
    roi   = m_port["roi_pct"]
    dd    = m_port["max_drawdown"]
    sh    = m_port["sharpe_annual"]
    spy_r = m_spy["roi_pct"]

    print()
    print("VEREDICTO:")
    if pf >= 1.5 and sh >= 1.0 and roi > spy_r:
        verdict = "VIABLE — supera al benchmark con riesgo controlado."
    elif pf >= 1.2 and sh >= 0.5:
        verdict = "MARGINAL — rentable pero no supera consistentemente al benchmark."
    elif roi > 0:
        verdict = "DEBIL — rentable pero con metricas insuficientes para operar con confianza."
    else:
        verdict = "NEGATIVO — no operar en real con esta configuracion."
    print(f"  {verdict}")

    # Guardar
    out = Path(__file__).parent / "logs" / f"backtest_portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "metrics": {"portfolio": m_port, "core": m_core, "satellite": m_sat, "benchmark_spy": m_spy},
            "profit_factor": round(pf, 2),
            "trades": all_trades,
        }, f, indent=2, default=str)
    print(f"\n  Guardado: {out.name}")
    print("=" * 65)


if __name__ == "__main__":
    main()

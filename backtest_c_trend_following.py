"""
ESTRATEGIA C — Trend Following (Donchian Channel 20 dias)
Logica: LONG cuando close supera el maximo de los ultimos 20 dias
        Salir cuando close cae bajo el minimo de los ultimos 10 dias
        Solo LONG (restriccion regulatoria Guatemala)
Mercado: GLD (oro), TLT (bonos), DBC (commodities), GDX (mineras oro)
         Diversificado para capturar tendencias en distintos activos
Datos: Yahoo Finance (10 anos)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import math
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

SYMBOLS      = ["GLD", "DBC", "GDX", "TLT"]
INITIAL_CAP  = 5_000.0
COMMISSION   = 0.0005
YEARS        = 10

DONCHIAN_ENTRY = 20   # breakout de maximo de N dias -> LONG
DONCHIAN_EXIT  = 10   # caer bajo minimo de N dias -> salir
ATR_PERIOD     = 20
ATR_RISK_MULT  = 2.0  # SL a 2x ATR del entry
RISK_PCT       = 0.02  # 2% del capital por trade


def compute_atr(df, period=20):
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def load_data(symbol):
    df = yf.download(symbol, period=f"{YEARS}y", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df = df[["Open","High","Low","Close","Volume"]].copy()
    df.columns = ["open","high","low","close","volume"]
    df.index.name = "date"
    df = df.dropna().reset_index()
    df["don_high"] = df["close"].shift(1).rolling(DONCHIAN_ENTRY).max()
    df["don_low"]  = df["close"].shift(1).rolling(DONCHIAN_EXIT).min()
    df["atr"]      = compute_atr(df, ATR_PERIOD)
    return df.dropna().reset_index(drop=True)


def backtest(symbol, df, capital):
    trades     = []
    open_trade = None
    peak       = capital
    max_dd     = 0.0

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]

        if open_trade:
            hit_sl   = row["low"] <= open_trade["sl"]
            hit_exit = row["close"] < row["don_low"]

            if hit_sl or hit_exit:
                exit_price = open_trade["sl"] if hit_sl else row["open"]
                pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
                pnl -= exit_price * open_trade["qty"] * COMMISSION
                capital += pnl
                peak   = max(peak, capital)
                max_dd = max(max_dd, (peak - capital) / peak)
                trades.append({
                    "date":   str(row["date"])[:10],
                    "symbol": symbol,
                    "entry":  round(open_trade["entry"], 4),
                    "exit":   round(exit_price, 4),
                    "days_held": open_trade["days"],
                    "pnl":    round(pnl, 4),
                    "reason": "STOP_LOSS" if hit_sl else "DONCHIAN_EXIT",
                    "capital": round(capital, 2),
                })
                open_trade = None
            else:
                open_trade["days"] += 1
                # trailing: subir SL al nuevo minimo Donchian
                open_trade["sl"] = max(open_trade["sl"], row["don_low"])
            continue

        # Entrada: breakout del maximo de 20 dias
        breakout = prev["close"] <= prev["don_high"] and row["close"] > row["don_high"]
        if breakout:
            entry = row["close"]
            atr   = row["atr"]
            sl    = entry - ATR_RISK_MULT * atr
            risk_amount = capital * RISK_PCT
            sl_dist = entry - sl
            if sl_dist <= 0:
                continue
            qty = risk_amount / sl_dist
            qty = min(qty, (capital * 0.95) / entry)
            if qty <= 0:
                continue
            fee = entry * qty * COMMISSION
            capital -= fee
            open_trade = {"entry": entry, "sl": sl, "qty": qty, "days": 1}

    if open_trade:
        exit_price = df.iloc[-1]["close"]
        pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
        pnl -= exit_price * open_trade["qty"] * COMMISSION
        capital += pnl
        trades.append({"date": str(df.iloc[-1]["date"])[:10], "symbol": symbol,
                        "entry": round(open_trade["entry"],4), "exit": round(exit_price,4),
                        "days_held": open_trade["days"], "pnl": round(pnl,4),
                        "reason": "END", "capital": round(capital,2)})

    return trades, capital, max_dd * 100


def metrics(all_trades, initial):
    if not all_trades:
        return None
    pnls   = [t["pnl"] for t in all_trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf     = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else float("inf")
    mean   = sum(pnls)/len(pnls)
    std    = pd.Series(pnls).std()
    sharpe = (mean/std*math.sqrt(len(pnls))) if std > 0 else 0
    final  = initial + sum(pnls)
    avg_days = sum(t.get("days_held",0) for t in all_trades) / len(all_trades)
    return {
        "trades": len(pnls), "win_rate": round(len(wins)/len(pnls)*100,1),
        "profit_factor": round(pf,2), "sharpe": round(sharpe,2),
        "roi_pct": round((final-initial)/initial*100,1),
        "avg_win": round(sum(wins)/len(wins),2) if wins else 0,
        "avg_loss": round(sum(losses)/len(losses),2) if losses else 0,
        "avg_days_held": round(avg_days,1),
        "final_capital": round(final,2),
    }


def main():
    print("=" * 60)
    print("ESTRATEGIA C — Trend Following Donchian (20/10 dias)")
    print(f"Mercado: {', '.join(SYMBOLS)} | {YEARS} anos de datos")
    print("=" * 60)

    cap_per = INITIAL_CAP / len(SYMBOLS)
    all_trades, max_dds = [], []

    for sym in SYMBOLS:
        df = load_data(sym)
        if df.empty:
            print(f"  {sym} — sin datos")
            continue
        trades, final_cap, max_dd = backtest(sym, df, cap_per)
        all_trades.extend(trades)
        max_dds.append(max_dd)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        pnl  = sum(t["pnl"] for t in trades)
        print(f"  {sym}: {len(trades)} trades | {wins}/{len(trades)} wins | P&L ${pnl:+.2f} | MaxDD {max_dd:.1f}%")

    m = metrics(all_trades, INITIAL_CAP)
    if not m:
        print("Sin trades.")
        return

    print()
    print(f"  Capital inicial:  ${INITIAL_CAP:>10,.2f}")
    print(f"  Capital final:    ${m['final_capital']:>10,.2f}")
    print(f"  ROI {YEARS} anos:    {m['roi_pct']:>+.1f}%")
    print(f"  Max Drawdown:     {max(max_dds):.1f}%")
    print(f"  Trades:           {m['trades']}")
    print(f"  Win rate:         {m['win_rate']}%")
    print(f"  Avg dias en trade:{m['avg_days_held']} dias")
    print(f"  Profit Factor:    {m['profit_factor']}  (>1.5 aceptable, >2 bueno)")
    print(f"  Sharpe:           {m['sharpe']}")

    verdict = ("VIABLE" if m["profit_factor"] >= 1.5 and m["sharpe"] >= 1.0
               else "MARGINAL" if m["profit_factor"] >= 1.0
               else "NEGATIVO")
    print(f"  VEREDICTO: {verdict}")

    out = Path(__file__).parent / "logs" / f"backtest_C_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"metrics": m, "trades": all_trades}, f, indent=2, default=str)
    print(f"  Guardado: {out.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()

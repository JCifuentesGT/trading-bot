"""
ESTRATEGIA A — Mean Reversion en ETFs de acciones
Lógica: RSI(2) < 10 con precio sobre EMA(200) -> LONG
        RSI(2) > 70 -> salir
Mercado: SPY, QQQ, IWM (ETFs de índice USA)
Datos: Yahoo Finance (10 años)
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

SYMBOLS        = ["SPY", "QQQ", "IWM"]
INITIAL_CAP    = 5_000.0
RISK_PCT       = 0.02      # 2% por trade
COMMISSION     = 0.0005    # 0.05% (broker acciones)
YEARS          = 10

RSI_ENTRY      = 10        # RSI(2) menor a esto -> entrar
RSI_EXIT       = 70        # RSI(2) mayor a esto -> salir (profit)
EMA_TREND      = 200       # filtro de tendencia
STOP_LOSS_PCT  = 0.07      # stop loss fijo: 7% bajo el entry
POS_SIZE_PCT   = 0.20      # 20% del capital por trade (max 5 posiciones simultaneas)


def compute_rsi(series, period=2):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def load_data(symbol):
    df = yf.download(symbol, period=f"{YEARS}y", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df = df[["Open","High","Low","Close","Volume"]].copy()
    df.columns = ["open","high","low","close","volume"]
    df.index.name = "date"
    df = df.dropna().reset_index()
    df["ema200"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["rsi2"]   = compute_rsi(df["close"], 2)
    return df.dropna().reset_index(drop=True)


def backtest(symbol, df, capital):
    trades     = []
    open_trade = None
    peak       = capital
    max_dd     = 0.0

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]

        # Gestionar trade abierto
        if open_trade:
            hit_sl       = row["low"] <= open_trade["sl"]
            trend_broken = row["close"] < row["ema200"]   # tendencia rota -> salir
            rsi_exit     = row["rsi2"] > RSI_EXIT

            if hit_sl or trend_broken or rsi_exit:
                if hit_sl:
                    exit_price = open_trade["sl"]
                    reason = "STOP_LOSS"
                else:
                    exit_price = row["open"]
                    reason = "RSI_EXIT" if rsi_exit else "TREND_BROKEN"

                pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
                pnl -= exit_price * open_trade["qty"] * COMMISSION
                capital += pnl
                peak = max(peak, capital)
                max_dd = max(max_dd, (peak - capital) / peak)
                trades.append({
                    "date":    str(row["date"])[:10],
                    "symbol":  symbol,
                    "entry":   round(open_trade["entry"], 4),
                    "exit":    round(exit_price, 4),
                    "pnl":     round(pnl, 4),
                    "reason":  reason,
                    "capital": round(capital, 2),
                })
                open_trade = None
            continue

        # Entrada: precio > EMA200 y RSI(2) < RSI_ENTRY
        above_trend  = prev["close"] > prev["ema200"]
        rsi_oversold = prev["rsi2"] < RSI_ENTRY
        if above_trend and rsi_oversold:
            entry = row["open"]
            sl    = entry * (1 - STOP_LOSS_PCT)
            qty   = (capital * POS_SIZE_PCT) / entry
            if qty <= 0:
                continue
            fee = entry * qty * COMMISSION
            capital -= fee
            open_trade = {"entry": entry, "qty": qty, "sl": sl}

    # Cerrar al final si queda abierto
    if open_trade:
        exit_price = df.iloc[-1]["close"]
        pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
        pnl -= exit_price * open_trade["qty"] * COMMISSION
        capital += pnl
        trades.append({"date": str(df.iloc[-1]["date"])[:10], "symbol": symbol,
                        "entry": round(open_trade["entry"],4), "exit": round(exit_price,4),
                        "pnl": round(pnl,4), "reason": "END", "capital": round(capital,2)})

    return trades, capital, max_dd * 100


def metrics(all_trades, initial):
    if not all_trades:
        return None
    pnls  = [t["pnl"] for t in all_trades]
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]
    pf    = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else float("inf")
    mean  = sum(pnls)/len(pnls)
    std   = pd.Series(pnls).std()
    sharpe= (mean/std*math.sqrt(len(pnls))) if std > 0 else 0
    final = initial + sum(pnls)
    return {
        "trades": len(pnls), "win_rate": round(len(wins)/len(pnls)*100,1),
        "profit_factor": round(pf,2), "sharpe": round(sharpe,2),
        "roi_pct": round((final-initial)/initial*100,1),
        "avg_win": round(sum(wins)/len(wins),2) if wins else 0,
        "avg_loss": round(sum(losses)/len(losses),2) if losses else 0,
        "final_capital": round(final,2),
    }


def main():
    print("=" * 60)
    print("ESTRATEGIA A — Mean Reversion ETFs (RSI-2 + EMA-200)")
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
        print("Sin trades generados.")
        return

    print()
    print(f"  Capital inicial:  ${INITIAL_CAP:>10,.2f}")
    print(f"  Capital final:    ${m['final_capital']:>10,.2f}")
    print(f"  ROI {YEARS} anos:     {m['roi_pct']:>+.1f}%")
    print(f"  Max Drawdown:     {max(max_dds):.1f}%")
    print(f"  Trades:           {m['trades']}")
    print(f"  Win rate:         {m['win_rate']}%")
    print(f"  Profit Factor:    {m['profit_factor']}  (>1.5 aceptable, >2 bueno)")
    print(f"  Sharpe:           {m['sharpe']}")

    verdict = ("VIABLE" if m["profit_factor"] >= 1.5 and m["sharpe"] >= 1.0
               else "MARGINAL" if m["profit_factor"] >= 1.0
               else "NEGATIVO")
    print(f"  VEREDICTO: {verdict}")

    out = Path(__file__).parent / "logs" / f"backtest_A_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"metrics": m, "trades": all_trades}, f, indent=2, default=str)
    print(f"  Guardado: {out.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()

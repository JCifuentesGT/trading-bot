"""
ESTRATEGIA B — Crypto Momentum Diario (EMA 200)
Logica: comprar cuando close cruza sobre EMA(200) en 1D
        salir cuando close cruza bajo EMA(200)
        Sin senales de 15m, sin sobreoperacion
Mercado: BTC, ETH (Bybit, datos reales)
Datos: Bybit API publica (sin auth)
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
import requests

SYMBOLS     = ["BTCUSDT", "ETHUSDT"]
INITIAL_CAP = 5_000.0
RISK_PCT    = 0.02
COMMISSION  = 0.001
YEARS       = 4          # maximo historico util en Bybit
DATA_DIR    = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def fetch_daily(symbol, days=1500):
    cache = DATA_DIR / f"{symbol}_1D_{days}d.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 43200:
        df = pd.read_csv(cache)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        print(f"  {symbol} 1D — cache ({len(df)} velas)")
        return df

    print(f"  {symbol} 1D — descargando...", end="", flush=True)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    all_rows = []
    cursor   = start_ms
    chunk_ms = 200 * 86_400_000   # 200 dias por request

    while cursor < end_ms:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={
            "category": "spot", "symbol": symbol, "interval": "D",
            "start": cursor, "end": min(cursor+chunk_ms, end_ms), "limit": 200,
        }, timeout=15)
        data = r.json()
        if data.get("retCode") == 0:
            all_rows.extend(data["result"]["list"])
        cursor += chunk_ms
        time.sleep(0.1)

    print(f" {len(all_rows)} velas")
    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["ts","open","high","low","close","volume","turnover"])
    df["ts"]    = pd.to_numeric(df["ts"])
    for c in ["open","high","low","close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.to_csv(cache, index=False)
    return df


def backtest(symbol, df, capital):
    df = df.copy()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df = df.dropna().reset_index(drop=True)

    trades     = []
    open_trade = None
    peak       = capital
    max_dd     = 0.0

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]

        if open_trade:
            # Salida: close cae bajo EMA200
            if row["close"] < row["ema200"]:
                exit_price = row["open"]
                pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
                pnl -= exit_price * open_trade["qty"] * COMMISSION
                capital += pnl
                peak   = max(peak, capital)
                max_dd = max(max_dd, (peak - capital) / peak)
                trades.append({
                    "date":   str(row["datetime"])[:10],
                    "symbol": symbol,
                    "entry":  round(open_trade["entry"], 2),
                    "exit":   round(exit_price, 2),
                    "days_held": open_trade["days"],
                    "pnl":    round(pnl, 4),
                    "reason": "EMA_CROSS_DOWN",
                    "capital": round(capital, 2),
                })
                open_trade = None
            else:
                open_trade["days"] += 1
            continue

        # Entrada: cruce EMA200 al alza
        crossed_up = prev["close"] < prev["ema200"] and row["close"] > row["ema200"]
        if crossed_up:
            entry = row["close"]
            qty   = (capital * 0.95) / entry   # casi todo el capital (position sizing simple)
            fee   = entry * qty * COMMISSION
            capital -= fee
            open_trade = {"entry": entry, "qty": qty, "days": 1}

    if open_trade:
        exit_price = df.iloc[-1]["close"]
        pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]
        pnl -= exit_price * open_trade["qty"] * COMMISSION
        capital += pnl
        trades.append({"date": str(df.iloc[-1]["datetime"])[:10], "symbol": symbol,
                        "entry": round(open_trade["entry"],2), "exit": round(exit_price,2),
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
    print("ESTRATEGIA B — Crypto Momentum Diario (EMA-200 cross)")
    print(f"Mercado: {', '.join(SYMBOLS)} | {YEARS} anos de datos")
    print("=" * 60)

    cap_per = INITIAL_CAP / len(SYMBOLS)
    all_trades, max_dds = [], []

    for sym in SYMBOLS:
        df = fetch_daily(sym, days=YEARS*365)
        if df.empty:
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
    print(f"  ROI {YEARS} anos:     {m['roi_pct']:>+.1f}%")
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

    out = Path(__file__).parent / "logs" / f"backtest_B_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"metrics": m, "trades": all_trades}, f, indent=2, default=str)
    print(f"  Guardado: {out.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()

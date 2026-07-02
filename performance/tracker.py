"""
tracker.py
Registra y calcula métricas de performance por portfolio.
Win rate, P&L acumulado, Sharpe ratio, max drawdown.
Persiste en JSON y genera resumen para los reportes diarios.
"""

import json
import os
import numpy as np
from datetime import datetime

TRADES_FILE = "performance/trades.json"


def _load_trades() -> list:
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            return json.load(f)
    return []


def _save_trades(trades: list):
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def record_trade(portfolio: str, symbol: str, signal: str, entry: float,
                 exit_price: float, qty: float, pnl: float, exit_reason: str):
    """Guarda un trade cerrado en el historial."""
    trades = _load_trades()
    trades.append({
        "timestamp":   str(datetime.utcnow()),
        "portfolio":   portfolio,
        "symbol":      symbol,
        "signal":      signal,
        "entry":       entry,
        "exit":        exit_price,
        "qty":         qty,
        "pnl":         round(pnl, 4),
        "exit_reason": exit_reason,
    })
    _save_trades(trades)


def calculate_metrics(portfolio: str) -> dict:
    """Calcula métricas completas para el portfolio dado."""
    trades = [t for t in _load_trades() if t["portfolio"] == portfolio]

    if not trades:
        return {"message": "Sin trades registrados aún."}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(pnls)
    win_rate = len(wins) / total if total > 0 else 0
    total_pnl = sum(pnls)

    # Sharpe ratio (anualizado, asumiendo ~40 trades/mes)
    arr = np.array(pnls)
    sharpe = float((arr.mean() / arr.std()) * np.sqrt(480)) if arr.std() > 0 and total >= 5 else None

    # Max drawdown
    cumulative = np.cumsum(arr)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_dd = float(drawdown.max()) if len(drawdown) > 0 else 0

    return {
        "portfolio":   portfolio,
        "total_trades": total,
        "win_rate":    round(win_rate, 3),
        "total_pnl":   round(total_pnl, 2),
        "avg_win":     round(np.mean(wins), 2) if wins else 0,
        "avg_loss":    round(np.mean(losses), 2) if losses else 0,
        "sharpe":      round(sharpe, 3) if sharpe else "insuficientes datos",
        "max_drawdown": round(max_dd, 2),
        "last_trade":  trades[-1]["timestamp"],
    }


def daily_report() -> str:
    """Genera reporte de texto para los dos portfolios."""
    lines = [f"=== Reporte diario {datetime.utcnow().strftime('%Y-%m-%d')} ===\n"]
    for portfolio in ["conservative", "aggressive"]:
        m = calculate_metrics(portfolio)
        lines.append(f"Portfolio: {portfolio.upper()}")
        if "message" in m:
            lines.append(f"  {m['message']}\n")
        else:
            lines.append(f"  Trades: {m['total_trades']} | Win rate: {m['win_rate']:.1%}")
            lines.append(f"  P&L total: ${m['total_pnl']:+.2f}")
            lines.append(f"  Sharpe: {m['sharpe']} | Max drawdown: ${m['max_drawdown']:.2f}\n")
    return "\n".join(lines)

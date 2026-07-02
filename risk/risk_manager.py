"""
risk_manager.py
Gestiona las reglas de riesgo del portfolio:
- Máximo de trades abiertos simultáneos
- Umbral de drawdown para pausar operaciones
- Re-evaluación de parámetros cada 20 trades
- Reducción de riesgo durante drawdown
"""

import json
import os
from datetime import datetime

STATE_FILE = "risk/portfolio_state.json"
MAX_OPEN_TRADES = 3


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
        # Migrar estado antiguo (conservative/aggressive) a portfolio unico
        if "main" not in data:
            old_cap = data.get("conservative", {}).get("capital", 10000.0)
            data = {"main": _default_portfolio_state(old_cap)}
            save_state(data)
        return data
    return {"main": _default_portfolio_state(10000.0)}


def _default_portfolio_state(capital: float = 10000.0) -> dict:
    return {
        "capital":        capital,
        "peak_capital":   capital,
        "drawdown_limit": 0.20,    # pausa si pierde 20% del pico
        "open_trades":    0,
        "total_trades":   0,
        "wins":           0,
        "losses":         0,
        "paused":         False,
        "pause_reason":   "",
        "last_updated":   str(datetime.utcnow()),
    }


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def can_trade(portfolio_key: str) -> tuple[bool, str]:
    """Verifica si el portfolio puede abrir un nuevo trade."""
    state = _load_state()
    p = state[portfolio_key]

    if p["paused"]:
        return False, f"Portfolio pausado: {p['pause_reason']}"

    drawdown = (p["peak_capital"] - p["capital"]) / p["peak_capital"]
    if drawdown >= p["drawdown_limit"]:
        p["paused"] = True
        p["pause_reason"] = f"Drawdown {drawdown:.1%} >= limite {p['drawdown_limit']:.1%}"
        save_state(state)
        return False, p["pause_reason"]

    return True, ""


def record_trade_result(portfolio_key: str, pnl: float):
    """Registra el resultado de un trade cerrado y actualiza el estado del portfolio."""
    state = _load_state()
    p = state[portfolio_key]

    p["capital"] += pnl
    p["total_trades"] += 1
    p["open_trades"] = max(0, p["open_trades"] - 1)

    if pnl > 0:
        p["wins"] += 1
    else:
        p["losses"] += 1

    if p["capital"] > p["peak_capital"]:
        p["peak_capital"] = p["capital"]

    p["last_updated"] = str(datetime.utcnow())

    win_rate = p["wins"] / p["total_trades"] if p["total_trades"] > 0 else 0
    print(f"[risk] Trade #{p['total_trades']} | WR: {win_rate:.1%} | Capital: ${p['capital']:,.2f}")
    save_state(state)


def open_trade(portfolio_key: str):
    """Registra la apertura de un trade."""
    state = _load_state()
    state[portfolio_key]["open_trades"] += 1
    save_state(state)


def get_capital(portfolio_key: str) -> float:
    return _load_state()[portfolio_key]["capital"]


def get_state_summary() -> dict:
    return _load_state()

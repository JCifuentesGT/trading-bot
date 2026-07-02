"""
signals.py — Estrategia EMA-200 Diario

Logica validada por backtest sobre 4 años de datos reales:
  - Entrada LONG: close cruza sobre EMA-200 en timeframe diario
  - Salida: close cae bajo EMA-200 (sin TP fijo — se deja correr la tendencia)
  - Stop loss de emergencia: 15% bajo el entry (proteccion ante crash)

Por que EMA-200 diario y no la estrategia anterior (RSI 15m):
  - Backtest 4 años: +466% (BTC+ETH) vs -10% de la estrategia de cruce RSI
  - Win rate 52% vs 27%, Profit Factor 47 vs 0.78
  - Menos trades (12-31/año vs 1487/año), menos ruido, más rentable
  - Timeframe diario evita el ruido intradiario y la sobreoperacion
"""

import pandas as pd
from core.market_data import get_klines

SIGNAL_LONG  = "LONG"
SIGNAL_NONE  = "NONE"
SIGNAL_EXIT  = "EXIT"

EMA_PERIOD   = 200
STOP_LOSS_PCT = 0.15   # 15% bajo el entry — solo para crashes extremos


def generate_signal(symbol: str, params: dict = None) -> dict:
    """
    Evalua la señal diaria para el simbolo.

    Retorna:
      signal = LONG  -> nueva entrada (cruce al alza de EMA-200)
      signal = EXIT  -> salir de la posicion abierta (caida bajo EMA-200)
      signal = NONE  -> mantener, sin cambios
    """
    df = get_klines(symbol, interval="D", limit=250)
    if df.empty or len(df) < EMA_PERIOD + 2:
        return {"signal": SIGNAL_NONE, "reason": "datos insuficientes"}

    df["ema200"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    df = df.dropna().reset_index(drop=True)

    prev = df.iloc[-2]
    last = df.iloc[-1]

    price   = float(last["close"])
    ema_val = float(last["ema200"])

    # Cruce al alza: ayer estaba bajo, hoy esta sobre la EMA
    crossed_up = float(prev["close"]) < float(prev["ema200"]) and price > ema_val

    # Precio cayo bajo la EMA (señal de salida)
    crossed_down = price < ema_val

    if crossed_up:
        stop_loss = price * (1 - STOP_LOSS_PCT)
        return {
            "signal":    SIGNAL_LONG,
            "symbol":    symbol,
            "entry":     round(price, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit": None,       # sin TP — se deja correr la tendencia
            "ema200":    round(ema_val, 4),
            "reason":    "cruce EMA-200 al alza",
        }

    if crossed_down:
        return {
            "signal": SIGNAL_EXIT,
            "symbol": symbol,
            "price":  round(price, 4),
            "ema200": round(ema_val, 4),
            "reason": "precio cayo bajo EMA-200",
        }

    return {
        "signal": SIGNAL_NONE,
        "symbol": symbol,
        "price":  round(price, 4),
        "ema200": round(ema_val, 4),
        "above_ema": price > ema_val,
    }


def should_exit_position(symbol: str, entry_price: float) -> tuple[bool, str]:
    """
    Evalua si una posicion abierta debe cerrarse.
    Retorna (debe_salir, razon).
    Usado por el ciclo de gestion de salidas.
    """
    sig = generate_signal(symbol)

    if sig["signal"] == SIGNAL_EXIT:
        return True, f"EMA-200 cruzada a la baja (precio {sig['price']} < EMA {sig['ema200']})"

    # Stop loss de emergencia (flash crash, evento extremo)
    if sig.get("price") and sig["price"] < entry_price * (1 - STOP_LOSS_PCT):
        return True, f"Stop loss de emergencia: -{STOP_LOSS_PCT*100:.0f}% desde entrada"

    return False, ""

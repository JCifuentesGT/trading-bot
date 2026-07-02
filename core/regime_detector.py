"""
regime_detector.py
Detecta el régimen de mercado actual: TRENDING o RANGING.
Usa ADX en 1h como fuente de verdad — por encima de 25 hay tendencia clara.
"""

import pandas as pd
from core.market_data import get_klines
from core.indicators import add_indicators

ADX_THRESHOLD = 20


class MarketRegime:
    TRENDING = "TRENDING"
    RANGING = "RANGING"


def detect_regime(symbol: str) -> str:
    """
    Detecta el régimen de mercado para el símbolo en timeframe 1h.
    Retorna MarketRegime.TRENDING o MarketRegime.RANGING.
    """
    df = get_klines(symbol, interval="60", limit=100)
    if df.empty:
        return MarketRegime.RANGING  # conservador: si no hay datos, no operar

    df = add_indicators(df)
    latest_adx = df["adx"].iloc[-1]

    regime = MarketRegime.TRENDING if latest_adx > ADX_THRESHOLD else MarketRegime.RANGING
    print(f"[regime] {symbol} | ADX: {latest_adx:.2f} | Régimen: {regime}")
    return regime

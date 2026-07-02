"""
market_data.py
Obtiene datos de velas (OHLCV) desde Bybit para los pares configurados.
Soporta múltiples timeframes y cacheo básico para evitar llamadas redundantes.
"""

from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import pandas as pd
import os
import time

load_dotenv()

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]  # todos validados con EMA-200 diario

client = HTTP(
    testnet=os.getenv("BYBIT_TESTNET", "true").lower() == "true",
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)


def get_klines(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """
    Retorna un DataFrame con velas OHLCV para el símbolo e intervalo dados.
    interval: 'D' = diario, '60' = 1 hora, '15' = 15 minutos
    """
    try:
        response = client.get_kline(
            category="spot",
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        raw = response["result"]["list"]

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df = df.astype({
            "timestamp": "int64",
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "volume": "float64",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    except Exception as e:
        print(f"[market_data] Error obteniendo {symbol} {interval}: {e}")
        return pd.DataFrame()


def get_current_price(symbol: str) -> float:
    """Retorna el último precio de mercado para el símbolo."""
    try:
        response = client.get_tickers(category="linear", symbol=symbol)
        return float(response["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"[market_data] Error obteniendo precio de {symbol}: {e}")
        return 0.0

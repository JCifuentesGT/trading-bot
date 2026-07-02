"""
optimizer.py
Walk-forward optimization semanal.
Prueba combinaciones de parámetros sobre los últimos 30 días y selecciona
la que maximiza el Sharpe ratio simulado. Se ejecuta cada lunes pre-mercado.
"""

import pandas as pd
import numpy as np
import json
import os
from itertools import product
from core.market_data import get_klines
from core.indicators import add_indicators

PARAMS_FILE = "strategy/optimized_params.json"

# Grid de parámetros a evaluar (lógica de tendencia + momentum)
PARAM_GRID = {
    "rsi_trigger":       [45, 50, 55],
    "atr_multiplier_sl": [1.5, 2.0],
    "atr_multiplier_tp": [3.0, 4.0],
}


def _simulate(df: pd.DataFrame, params: dict) -> float:
    """
    Simula la estrategia sobre el DataFrame y retorna el Sharpe ratio.
    Lógica: tendencia establecida (EMA) + RSI cruza el trigger (momentum).
    """
    returns = []
    trig = params["rsi_trigger"]
    for i in range(1, len(df) - 1):
        prev, last = df.iloc[i - 1], df.iloc[i]
        aligned_up = last["ema20"] > last["ema50"]
        rsi_cross_up = prev["rsi"] <= trig and last["rsi"] > trig

        if aligned_up and rsi_cross_up:
            sl = last["close"] - last["atr"] * params["atr_multiplier_sl"]
            tp = last["close"] + last["atr"] * params["atr_multiplier_tp"]
            future = df.iloc[i + 1]["close"]
            if future >= tp:
                returns.append(params["atr_multiplier_tp"])
            elif future <= sl:
                returns.append(-params["atr_multiplier_sl"])

    if len(returns) < 5:
        return -999.0

    arr = np.array(returns)
    return float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0


def run_optimization(symbols: list) -> dict:
    """
    Corre la optimización sobre los símbolos dados.
    Guarda y retorna los mejores parámetros encontrados.
    """
    print("[optimizer] Iniciando walk-forward optimization...")
    all_dfs = []
    for symbol in symbols:
        df = get_klines(symbol, interval="60", limit=720)  # ~30 días en 1h
        if not df.empty:
            all_dfs.append(add_indicators(df))

    if not all_dfs:
        print("[optimizer] Sin datos suficientes, usando parámetros por defecto.")
        return _load_params()

    combined = pd.concat(all_dfs, ignore_index=True)

    best_sharpe = -999.0
    best_params = None

    keys = list(PARAM_GRID.keys())
    for values in product(*PARAM_GRID.values()):
        params = dict(zip(keys, values))
        sharpe = _simulate(combined, params)
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = params

    print(f"[optimizer] Mejores parámetros encontrados | Sharpe simulado: {best_sharpe:.3f}")
    print(f"[optimizer] Params: {best_params}")

    _save_params(best_params)
    return best_params


def _save_params(params: dict):
    os.makedirs(os.path.dirname(PARAMS_FILE), exist_ok=True)
    with open(PARAMS_FILE, "w") as f:
        json.dump(params, f, indent=2)


def _load_params() -> dict:
    """Carga parámetros guardados o retorna los defaults."""
    from strategy.signals import DEFAULT_PARAMS
    if os.path.exists(PARAMS_FILE):
        with open(PARAMS_FILE) as f:
            return json.load(f)
    return DEFAULT_PARAMS.copy()

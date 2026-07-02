"""
position_sizing.py
Calcula el tamaño de posición para SPOT.

En spot el riesgo se controla por la distancia al stop loss, PERO con un tope:
no se puede gastar más capital del disponible (no hay apalancamiento). Por eso
el notional (qty * precio) se limita a una fracción del capital del portfolio,
para que quepan varias posiciones simultáneas sin sobre-asignar la billetera.
"""

# Fracción máxima del capital del portfolio que puede ocupar UNA posición.
# Con tope de 3 posiciones simultáneas, 0.25 deja margen holgado.
MAX_POSITION_FRAC = 0.25

# Valor mínimo de orden en Bybit spot (USDT). Por debajo de esto la orden falla.
MIN_ORDER_USDT = 5.0


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    min_qty: float = 0.001,
) -> float:
    """
    Calcula el tamaño de posición spot en unidades del activo base.

    Lógica:
    1. notional ideal = riesgo / distancia_al_stop_en_% (para que tocar el SL
       cueste exactamente risk_pct del capital)
    2. se limita ese notional a MAX_POSITION_FRAC del capital (límite spot)
    3. se convierte a cantidad de activo y se redondea al step permitido

    Returns:
        Cantidad en unidades del activo, o 0.0 si no es viable.
    """
    risk_amount = capital * risk_pct
    price_distance = abs(entry_price - stop_loss)

    if price_distance <= 0 or entry_price <= 0:
        return 0.0

    # Notional que arriesga exactamente risk_amount al tocar el SL
    stop_distance_frac = price_distance / entry_price
    ideal_notional = risk_amount / stop_distance_frac

    # Tope spot: no exceder una fracción del capital
    max_notional = capital * MAX_POSITION_FRAC
    notional = min(ideal_notional, max_notional)

    # Verificar mínimo de orden
    if notional < MIN_ORDER_USDT:
        return 0.0

    qty = notional / entry_price

    # Redondear hacia abajo al step permitido
    qty = round(qty - (qty % min_qty), 6)
    if qty < min_qty:
        return 0.0
    return qty


# Cantidades mínimas (step) por par en Bybit
MIN_QTY = {
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.1,
    "BTCUSDT": 0.001,
    "XRPUSDT": 0.1,
}

"""
executor.py
Ejecuta órdenes SPOT en Bybit (testnet o real según configuración).

Importante: los derivados (futuros perpetuos) están bloqueados por restricción
regulatoria en algunas regiones (ErrCode 10024). Operamos SPOT, que sí está
permitido. En spot solo se puede operar LONG (comprar y luego vender); no hay
posiciones cortas ni apalancamiento, y el stop loss / take profit los gestiona
el propio bot vigilando el precio.
"""

from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os

load_dotenv()

client = HTTP(
    testnet=os.getenv("BYBIT_TESTNET", "true").lower() == "true",
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)


def buy_spot(symbol: str, qty: float) -> dict:
    """
    Compra al contado (market buy). qty en unidades del activo base (ej. BTC).

    Returns dict con success, orderId, filled_qty (cantidad real recibida tras
    comisiones) o error.
    """
    try:
        response = client.place_order(
            category="spot",
            symbol=symbol,
            side="Buy",
            orderType="Market",
            qty=str(qty),
            marketUnit="baseCoin",  # qty se interpreta en moneda base
        )
        order_id = response["result"].get("orderId", "")

        # La comisión en spot se descuenta del activo recibido, así que la
        # cantidad real disponible para vender es un poco menor que qty.
        filled = _get_filled_qty(symbol, order_id, fallback=qty)
        print(f"[executor] COMPRA {symbol} qty={qty} (recibido ~{filled}) | ID: {order_id}")
        return {"success": True, "orderId": order_id, "filled_qty": filled}

    except Exception as e:
        print(f"[executor] Error comprando {symbol}: {e}")
        return {"success": False, "error": str(e)}


def sell_spot(symbol: str, qty: float) -> dict:
    """Vende al contado (market sell). qty en unidades del activo base."""
    try:
        response = client.place_order(
            category="spot",
            symbol=symbol,
            side="Sell",
            orderType="Market",
            qty=str(qty),
            marketUnit="baseCoin",
        )
        order_id = response["result"].get("orderId", "")
        print(f"[executor] VENTA {symbol} qty={qty} | ID: {order_id}")
        return {"success": True, "orderId": order_id}

    except Exception as e:
        print(f"[executor] Error vendiendo {symbol}: {e}")
        return {"success": False, "error": str(e)}


def _get_filled_qty(symbol: str, order_id: str, fallback: float) -> float:
    """Consulta la cantidad realmente ejecutada de una orden."""
    try:
        r = client.get_order_history(category="spot", symbol=symbol, orderId=order_id)
        lst = r["result"]["list"]
        if lst:
            cum = float(lst[0].get("cumExecQty", 0))
            fee = float(lst[0].get("cumExecFee", 0))
            # En compra spot la comisión se cobra en base coin
            net = cum - fee
            if net > 0:
                return net
    except Exception:
        pass
    # Si no se puede consultar, asumir qty menos un margen de comisión típico (0.1%)
    return round(fallback * 0.999, 6)


def get_spot_balance(coin: str) -> float:
    """Retorna el balance disponible de una moneda en la cuenta unificada."""
    try:
        r = client.get_wallet_balance(accountType="UNIFIED")
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
    except Exception as e:
        print(f"[executor] Error consultando balance de {coin}: {e}")
    return 0.0

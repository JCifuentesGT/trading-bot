"""
main.py — Trading Bot EMA-200 Diario (SPOT, long-only)

Estrategia validada por backtest 4 años (datos reales Bybit):
  BTC: +466% | ETH: +460% | SOL: +973% | XRP: +639%
  Profit Factor promedio: 25+ | Sharpe: 2.9

Ciclo diario (00:05 UTC — tras el cierre de la vela diaria):
  1. Gestiona salidas: cierra si precio cayo bajo EMA-200
  2. Busca entradas: compra si precio cruzo sobre EMA-200

Sin TP fijo — se deja correr la tendencia hasta que la EMA-200 se rompa.
Stop loss de emergencia al 15% bajo el entry (solo para crashes extremos).
"""

import sys
import os
import json
import time
import schedule
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core.market_data import PAIRS, get_current_price
from core.executor import buy_spot, sell_spot
from strategy.signals import generate_signal, should_exit_position, SIGNAL_LONG
from risk.risk_manager import can_trade, open_trade, record_trade_result, get_capital
from performance.tracker import record_trade, daily_report

PORTFOLIO        = "main"
OPEN_TRADES_FILE = "risk/open_trades.json"
MAX_POSITIONS    = 2          # maximo 2 posiciones simultaneas
POS_SIZE_PCT     = 0.40       # 40% del capital por posicion

open_trades: dict = {}


def _load_open_trades():
    global open_trades
    if os.path.exists(OPEN_TRADES_FILE):
        try:
            with open(OPEN_TRADES_FILE, encoding="utf-8") as f:
                open_trades = json.load(f)
            if open_trades:
                print(f"[bot] {len(open_trades)} posicion(es) recuperada(s) de disco")
        except Exception as e:
            print(f"[bot] No se pudo cargar open_trades: {e}")
            open_trades = {}


def _save_open_trades():
    os.makedirs(os.path.dirname(OPEN_TRADES_FILE), exist_ok=True)
    with open(OPEN_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(open_trades, f, indent=2)


def run_cycle():
    """Ciclo principal — se ejecuta una vez al dia a las 00:05 UTC."""
    now = datetime.now(timezone.utc)
    print(f"\n[bot] {now.strftime('%Y-%m-%d %H:%M UTC')} — Ciclo diario")

    # FASE 1: gestionar salidas de posiciones abiertas
    for symbol in list(open_trades.keys()):
        _manage_exit(symbol)

    # FASE 2: buscar nuevas entradas
    n_open = len(open_trades)
    if n_open >= MAX_POSITIONS:
        print(f"[bot] {n_open}/{MAX_POSITIONS} posiciones abiertas — sin nuevas entradas")
        return

    for symbol in PAIRS:
        if symbol in open_trades:
            print(f"[bot] {symbol} — posicion ya abierta, ignorando")
            continue

        sig = generate_signal(symbol)

        if sig["signal"] != SIGNAL_LONG:
            status = "sobre EMA" if sig.get("above_ema") else "bajo EMA"
            print(f"[bot] {symbol} — sin señal ({status} {sig.get('ema200', '')})")
            continue

        print(f"[bot] {symbol} SEÑAL LONG | entry={sig['entry']} | EMA200={sig['ema200']} | SL={sig['stop_loss']}")

        ok, reason = can_trade(PORTFOLIO)
        if not ok:
            print(f"[bot] Portfolio pausado: {reason}")
            break

        capital = get_capital(PORTFOLIO)
        qty_usdt = capital * POS_SIZE_PCT
        qty = qty_usdt / sig["entry"]

        from risk.position_sizing import MIN_QTY
        min_qty = MIN_QTY.get(symbol, 0.001)
        qty = round(qty - (qty % min_qty), 6)
        if qty < min_qty:
            print(f"[bot] {symbol} | qty {qty} menor al minimo {min_qty}, omitiendo")
            continue

        result = buy_spot(symbol, qty)
        if not result["success"]:
            print(f"[bot] Error comprando {symbol}: {result.get('error')}")
            continue

        step = min_qty
        filled_raw = result.get("filled_qty", qty)
        filled = round(filled_raw - (filled_raw % step), 6)
        if filled < step:
            filled = step

        open_trades[symbol] = {
            "symbol":    symbol,
            "portfolio": PORTFOLIO,
            "entry":     sig["entry"],
            "stop_loss": sig["stop_loss"],
            "ema200_at_entry": sig["ema200"],
            "qty":       filled,
            "order_id":  result["orderId"],
            "opened_at": str(now),
        }
        open_trade(PORTFOLIO)
        _save_open_trades()
        print(f"[bot] ABIERTO {symbol} | qty={filled} | capital desplegado ~${qty_usdt:.0f}")

        if len(open_trades) >= MAX_POSITIONS:
            print(f"[bot] Maximo de posiciones alcanzado ({MAX_POSITIONS})")
            break


def _manage_exit(symbol: str):
    """Cierra la posicion si el precio cayo bajo la EMA-200 o si toco el SL."""
    trade = open_trades[symbol]
    price = get_current_price(symbol)
    if price <= 0:
        return

    should_exit, reason = should_exit_position(symbol, trade["entry"])

    # Tambien verificar stop loss de emergencia por precio en tiempo real
    if not should_exit and price <= trade["stop_loss"]:
        should_exit = True
        reason = f"Stop loss hit: precio {price} <= SL {trade['stop_loss']}"

    if not should_exit:
        pct = (price - trade["entry"]) / trade["entry"] * 100
        print(f"[bot] {symbol} | precio {price} | P&L {pct:+.1f}% desde entrada | manteniendo")
        return

    result = sell_spot(symbol, trade["qty"])
    if not result["success"]:
        print(f"[bot] Error vendiendo {symbol}: {result.get('error')} — reintentando proximo ciclo")
        return

    pnl = (price - trade["entry"]) * trade["qty"]
    exit_reason = "EMA_CROSS_DOWN" if "EMA" in reason else "STOP_LOSS"

    record_trade(
        portfolio=PORTFOLIO,
        symbol=symbol,
        signal="LONG",
        entry=trade["entry"],
        exit_price=price,
        qty=trade["qty"],
        pnl=pnl,
        exit_reason=exit_reason,
    )
    record_trade_result(PORTFOLIO, pnl)
    print(f"[bot] CERRADO {symbol} | {exit_reason} | salida={price} | PnL: ${pnl:+.2f}")
    print(f"[bot] Razon: {reason}")
    del open_trades[symbol]
    _save_open_trades()


def end_of_day_report():
    report = daily_report()
    print(report)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/report_{date_str}.txt", "w", encoding="utf-8") as f:
        f.write(report)


def main():
    print("=" * 55)
    print("Trading Bot EMA-200 Diario — BTC/ETH/SOL/XRP (SPOT)")
    print("Estrategia: comprar cruce EMA-200, vender al romper")
    print(f"Max posiciones: {MAX_POSITIONS} | Tamaño: {POS_SIZE_PCT*100:.0f}% por pos.")
    print("=" * 55)

    _load_open_trades()

    # Ciclo diario a las 00:05 UTC (5 min despues del cierre de la vela diaria)
    schedule.every().day.at("00:05").do(run_cycle)
    schedule.every().day.at("22:00").do(end_of_day_report)

    # Ejecutar ciclo inmediatamente al arrancar
    run_cycle()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()

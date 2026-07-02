# Trading Bot — Contexto para Claude

## Que es este proyecto
Bot de trading automatizado en Bybit operando BTC/ETH/SOL/XRP en SPOT.
Estrategia validada por backtest sobre 4 anos de datos reales.

## Estrategia actual (cambiada 2026-06-22)
**EMA-200 Diario** — la estrategia anterior (RSI crossover 15m) fue descartada
tras backtesting: perdia dinero consistentemente (PF 0.78, -10% en 1 año).

La nueva estrategia:
- Entrada LONG: precio cruza sobre EMA(200) en timeframe diario
- Salida: precio cae bajo EMA(200) — sin TP fijo, se deja correr la tendencia
- Stop loss de emergencia: 15% bajo el entry (solo para crashes extremos)
- Ciclo: una vez al dia a las 00:05 UTC (tras cierre de vela diaria)

## Resultados del backtest (4 años, datos reales Bybit)
| Par     | ROI     | Win rate | Trades |
|---------|---------|----------|--------|
| BTCUSDT | +466%   | 42%      | 31     |
| ETHUSDT | +460%   | 71%      | 17     |
| SOLUSDT | +973%   | 52%      | 33     |
| XRPUSDT | +640%   | 48%      | 42     |

## Configuracion
- Capital: 10,000 USDT en Unified Trading (Bybit Testnet)
- Portfolio: unico (main) — eliminados conservative/aggressive
- Max posiciones simultaneas: 2
- Tamaño por posicion: 40% del capital
- Drawdown limit: 20% (bot pausa si llega a eso)

## Estado actual
- Bot: CORRIENDO via Windows Task Scheduler (tarea "TradingBot")
- Logs en tiempo real: logs/bot.log
- Reportes diarios: logs/report_YYYY-MM-DD.txt

## Archivos clave
- main.py — orquestador diario
- strategy/signals.py — logica EMA-200
- core/market_data.py — conexion Bybit SPOT
- risk/risk_manager.py — gestion de capital (portfolio "main")
- performance/tracker.py — metricas y reportes

## Restriccion regulatoria Guatemala
Los derivados (futuros perpetuos) estan BLOQUEADOS (Bybit ErrCode 10024).
Solo SPOT disponible: unicamente LONG, sin apalancamiento.

## Archivos de backtesting (no parte del bot en vivo)
- backtest.py — backtest RSI original (estrategia descartada)
- backtest_a/b/c — estrategias alternativas comparadas
- backtest_portfolio.py — portfolio C+B combinado
- optimize.py / optimize_core.py — optimizadores
- data/ — cache de datos historicos

## Como arrancar el bot
cd C:\Users\jccif\OneDrive\Documentos\Code\projects\trading-bot
python main.py

# Trading Bot — Notas técnicas

## Decisiones de diseño
- Bybit Testnet elegido sobre IBKR por simplicidad de API y paper trading nativo
- Pares ETH/SOL/BTC/XRP
- Portfolio split: misma estrategia, diferente % de riesgo para comparación limpia

## SPOT en vez de derivados (CAMBIO 2026-06-04)
PROBLEMA: los derivados (futuros perpetuos, category 'linear') están BLOQUEADOS
por restricción regulatoria en Guatemala (Bybit ErrCode 10024). El bot generaba
señales correctamente pero TODAS las órdenes eran rechazadas.

SOLUCIÓN: se migró toda la ejecución a SPOT (category 'spot'), que sí está
permitido. Implicaciones:
- Solo LONG (en spot no se puede vender en corto). Señales SHORT se omiten.
- Sin apalancamiento (1:1 siempre).
- SL/TP los gestiona el bot: cada ciclo revisa el precio y vende si toca TP/SL.
- Tamaño de posición con tope de capital (MAX_POSITION_FRAC=0.25), no se puede
  gastar más de lo que hay.
- Comisión spot se descuenta en moneda base; el bot calcula filled_qty real y
  redondea al step para poder vender sin error.
- Posiciones abiertas se persisten en risk/open_trades.json (sobreviven reinicios).
VALIDADO con órdenes reales de compra/venta y disparo de TP en testnet.

## Bug corregido
- UnicodeEncodeError al loguear errores con caracteres como → : se forzó UTF-8
  en sys.stdout/stderr al inicio de main.py.

## Librerías
- pybit 5.16 — SDK oficial Bybit
- pandas 3.0 — procesamiento de datos
- python-dotenv — manejo seguro de API keys
- schedule — ejecución periódica

## API Keys
- Archivo: .env (nunca en git)
- Permisos activos: Unified Trading (Read-Write) + Assets (Account Transfer)
- Entorno: testnet=True

## Estrategia (REVISADA 2026-06-03)
PROBLEMA DETECTADO: la versión original esperaba el cruce EXACTO de EMA20/50 en 15m
+ filtro RSI 40-60. Diagnóstico sobre ~500 velas: solo 4-5 cruces, y el filtro RSI
mataba casi todos (ETH 1 de 5, SOL 0 de 4). Resultado: 0 trades en varios días.

NUEVA LÓGICA (tendencia + momentum):
- 1h: EMA 20/50 define el sesgo de tendencia
- 15m: entrada cuando RSI cruza el nivel trigger (50) EN DIRECCIÓN del sesgo,
  con estado de EMA 15m alineado
- Régimen: ADX > 20 en 1h (bajado de 25)
- Backtest: ~4 señales/día combinadas (ETH ~3, SOL ~1) — disciplinado, sin sequía
  ni sobre-operación

Parámetros actuales (strategy/optimized_params.json, se optimiza cada lunes):
Defaults: EMA 20/50, rsi_trigger 50, ATR SL 1.5x, TP 3.0x (ratio 1:2)

## Régimen de mercado al inicio
- ETH: TRENDING (ADX ~40)
- SOL: TRENDING (ADX ~61)

## Ejecución durable
- Windows Task Scheduler: tarea "TradingBot" arranca el bot al iniciar Windows
- Script: start_bot.bat (usa python -u para logging sin buffer)
- Reinicia automáticamente hasta 5 veces si el proceso muere
- Logs: logs/bot.log
- Comandos: Start-ScheduledTask / Stop-ScheduledTask -TaskName 'TradingBot'

## Revisión semanal con IA (pendiente de activar)
- Módulo: strategy/ai_review.py — usa Claude (opus-4-8) para interpretar
  performance y ajustar parámetros con criterio
- Se ejecuta los lunes junto al optimizador mecánico
- Requiere ANTHROPIC_API_KEY en .env + saldo en la cuenta Anthropic
- Incluye prompt caching, validación de rangos y log de auditoría (logs/ai_reviews.jsonl)
- Estimado de costo: < $1/mes

## Pendientes técnicos
- [ ] Activar revisión con IA (pago pendiente)
- [ ] Notificaciones cuando el bot pausa por drawdown
- [ ] Dashboard web simple para ver métricas en tiempo real
- [ ] Backtesting histórico antes de pasar a cuenta real

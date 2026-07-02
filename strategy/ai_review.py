"""
ai_review.py
Revisión semanal de la estrategia usando Claude.

A diferencia de optimizer.py (que optimiza parámetros de forma mecánica sobre
una grilla), este módulo le entrega a Claude los datos reales de performance
para que los interprete con criterio, identifique problemas de fondo y proponga
ajustes razonados. Los ajustes se aplican automáticamente a optimized_params.json.

Requiere ANTHROPIC_API_KEY en el archivo .env.
"""

import os
import json
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from performance.tracker import calculate_metrics, _load_trades
from risk.risk_manager import get_state_summary
from strategy.optimizer import _load_params, _save_params

load_dotenv()

MODEL = "claude-opus-4-8"
REVIEW_LOG = "logs/ai_reviews.jsonl"

# Prompt de sistema estable (se cachea entre llamadas para reducir costos).
SYSTEM_PROMPT = """Eres un analista cuantitativo senior revisando un bot de trading de criptomonedas en paper trading (Bybit Testnet).

Contexto del sistema:
- Opera ETH/USDT y SOL/USDT, corto plazo (señal en 15m confirmada por tendencia en 1h)
- Estrategia: tendencia (EMA 20/50 en 1h) + momentum (RSI cruza nivel trigger en 15m) + stop loss y take profit por ATR (ratio objetivo 1:2)
- Solo opera en mercado con tendencia (filtro ADX > 20)
- Dos portfolios en paralelo con la MISMA estrategia, distinto riesgo: conservador (1%) y agresivo (3%)
- Objetivo: win rate ~40%, Sharpe mínimo aceptable 1.0 (válido a partir de 50 trades)

Tu tarea cada semana:
1. Interpretar las métricas reales (no solo números: el porqué detrás de ellos)
2. Detectar problemas de fondo: sobre-operación, filtros mal calibrados, régimen de mercado adverso, etc.
3. Proponer ajustes concretos y conservadores a los parámetros de la estrategia

Parámetros ajustables y sus rangos seguros:
- ema_fast: entero 10-30 (debe ser menor que ema_slow)
- ema_slow: entero 35-70
- rsi_trigger: entero 45-55 (nivel que el RSI debe cruzar para confirmar momentum)
- atr_multiplier_sl: float 1.0-3.0 (distancia del stop loss)
- atr_multiplier_tp: float 2.0-6.0 (debe mantener ratio >= 2x el SL)

Principios:
- Cambios graduales, nunca radicales. Si los datos son insuficientes (< 20 trades), NO cambies nada.
- Prioriza minimizar riesgo sobre maximizar ganancia.
- Si la estrategia funciona bien, no la toques. Justifica cada cambio con los datos."""

# Esquema de salida estructurada: Claude debe responder exactamente con esta forma.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnostico": {
            "type": "string",
            "description": "Interpretación de la performance de la semana y problemas detectados",
        },
        "cambiar_parametros": {
            "type": "boolean",
            "description": "true solo si hay datos suficientes y los cambios están justificados",
        },
        "parametros_nuevos": {
            "type": "object",
            "properties": {
                "ema_fast": {"type": "integer"},
                "ema_slow": {"type": "integer"},
                "rsi_trigger": {"type": "integer"},
                "atr_multiplier_sl": {"type": "number"},
                "atr_multiplier_tp": {"type": "number"},
            },
            "required": ["ema_fast", "ema_slow", "rsi_trigger",
                         "atr_multiplier_sl", "atr_multiplier_tp"],
            "additionalProperties": False,
        },
        "justificacion": {
            "type": "string",
            "description": "Por qué estos cambios (o por qué no cambiar nada)",
        },
        "alertas": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Advertencias para el operador humano, si las hay",
        },
    },
    "required": ["diagnostico", "cambiar_parametros", "parametros_nuevos",
                 "justificacion", "alertas"],
    "additionalProperties": False,
}


def _build_context() -> str:
    """Reúne todos los datos de performance para entregarle a Claude."""
    metrics = {p: calculate_metrics(p) for p in ["conservative", "aggressive"]}
    state = get_state_summary()
    current_params = _load_params()
    recent_trades = _load_trades()[-30:]  # últimos 30 trades como muestra

    return json.dumps({
        "fecha_revision": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "parametros_actuales": current_params,
        "metricas": metrics,
        "estado_portfolios": state,
        "trades_recientes": recent_trades,
    }, indent=2, default=str)


def run_ai_review(apply_changes: bool = True) -> dict:
    """
    Ejecuta la revisión con Claude. Retorna el análisis y, si procede,
    aplica los nuevos parámetros.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ai_review] Falta ANTHROPIC_API_KEY en .env — revisión omitida.")
        return {"error": "no_api_key"}

    client = anthropic.Anthropic(api_key=api_key)
    context = _build_context()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # cachea el prompt estable
            }],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{
                "role": "user",
                "content": f"Revisa la performance de esta semana y decide si ajustar la estrategia.\n\nDatos:\n{context}",
            }],
        )
    except anthropic.APIError as e:
        print(f"[ai_review] Error de API: {e}")
        return {"error": str(e)}

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    review = json.loads(text)

    # Registrar la revisión completa
    _log_review(review, response.usage)

    print("\n" + "=" * 60)
    print("REVISIÓN SEMANAL CON IA")
    print("=" * 60)
    print(f"\nDiagnóstico:\n{review['diagnostico']}\n")
    print(f"Justificación:\n{review['justificacion']}\n")
    if review.get("alertas"):
        print("Alertas:")
        for a in review["alertas"]:
            print(f"  - {a}")

    # Aplicar cambios si Claude lo recomienda y están validados
    if apply_changes and review.get("cambiar_parametros"):
        nuevos = review["parametros_nuevos"]
        if _validate_params(nuevos):
            _save_params(nuevos)
            print(f"\n[ai_review] Parámetros actualizados: {nuevos}")
        else:
            print("\n[ai_review] Parámetros propuestos fuera de rango — NO aplicados.")
            review["alertas"].append("Parámetros propuestos rechazados por validación de seguridad.")
    else:
        print("\n[ai_review] Sin cambios esta semana.")

    print("=" * 60 + "\n")
    return review


def _validate_params(p: dict) -> bool:
    """Valida que los parámetros propuestos estén en rangos seguros."""
    try:
        return (
            10 <= p["ema_fast"] <= 30
            and 35 <= p["ema_slow"] <= 70
            and p["ema_fast"] < p["ema_slow"]
            and 45 <= p["rsi_trigger"] <= 55
            and 1.0 <= p["atr_multiplier_sl"] <= 3.0
            and 2.0 <= p["atr_multiplier_tp"] <= 6.0
            and p["atr_multiplier_tp"] >= p["atr_multiplier_sl"] * 2
        )
    except (KeyError, TypeError):
        return False


def _log_review(review: dict, usage):
    """Guarda cada revisión en un log para auditoría histórica."""
    os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
    entry = {
        "timestamp": str(datetime.now(timezone.utc)),
        "review": review,
        "tokens": {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0),
        },
    }
    with open(REVIEW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


if __name__ == "__main__":
    run_ai_review()

"""
core/pipeline_health.py
=======================
Health check explícito del pipeline de Zohar v4.

Garantiza que, antes de procesar, el LLM local (llama-server) esté realmente
vivo y operativo, priorizándolo sobre los fallbacks remotos (Mistral / Gemini).
Se invoca al inicio de cada etapa que depende del LLM (enriquecimiento,
inferencia, resumen) y desde el startup de la API.

No bloquea el pipeline: si el local no está sano, loguea la decisión y permite
el fallback. Si se quiere fallar fuerte, usar raise_on_unhealthy=True.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ensure_pipeline_llm_ready(raise_on_unhealthy: bool = False) -> dict:
    """
    Check explícito al inicio del pipeline.

    Retorna:
        {
          "provider": str,            # backend que se usará (llama-server | mistral | gemini | heuristic)
          "model": str,
          "local_healthy": bool,      # si el llama-server local pasó health + probe
          "prefer_local": bool,       # True si el pipeline debe forzar el local en cada llamada
          "local_health": dict,       # detalle de is_local_llm_healthy()
        }
    """
    from core.llm_client import assert_local_llm_ready
    res = assert_local_llm_ready(raise_on_unhealthy=raise_on_unhealthy)
    res["prefer_local"] = bool(res.get("local_health", {}).get("healthy"))
    return res


def readiness_report() -> str:
    """Devuelve un string legible para logs/dashboard sobre el estado del LLM."""
    from core.llm_client import is_local_llm_healthy
    h = is_local_llm_healthy()
    if h["healthy"]:
        return (
            f"LLM LOCAL OK  url={h['url']} model={h['model']} "
            f"health={h['health_status']} probe={h['probe_ms']}ms"
        )
    return (
        f"LLM LOCAL NO DISPONIBLE  url={h['url']} "
        f"health={h['health_status']} error={h.get('error')}"
    )

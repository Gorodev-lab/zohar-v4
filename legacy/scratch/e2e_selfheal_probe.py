"""Prueba end-to-end Fase 2: inferencia local larga REAL con el self-healing
loop NUEVO vivo, para confirmar que NO reinicia el contenedor mientras hay
inferencia en curso (LLM_INFERENCE_IN_PROGRESS pausa el restart).

- Usa el llama-server real (maritime_llama_cpp) en http://localhost:8083.
- Lanza el loop nuevo de api/main.py en un event loop propio, con los
  asyncio.sleep acelerados a 8s (gracia 8s + ciclos 8s) para que en ~50s de
  inferencia acumule >=3 ciclos unhealthy DENTRO de la ventana de inferencia.
- restart_llama_container se INTERCEPTA (contador) para no destruir el server
  real durante la prueba; así la inferencia puede terminar y confirmamos que
  el contador queda en 0 (el loop nuevo pausó, no reinició).
- Inferencia: _complete_local con n_predict=512 y prompt largo (~40-50s).
"""
import asyncio
import logging
import time
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Asegurar que el proyecto esté en sys.path (el script vive en scratch/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("e2e")

import api.main as m
from core.llm_client import LLM_INFERENCE_IN_PROGRESS, _complete_local

# Contador de reinicios del self-healing (interceptamos para no matar el server)
restart_calls = {"n": 0}

async def fake_restart_llama_container():
    restart_calls["n"] += 1
    logger.warning(">>> restart_llama_container() LLAMADO (mock) n=%d", restart_calls["n"])

async def run():
    # Interceptar el restart real
    m.restart_llama_container = fake_restart_llama_container

    # Simular que /health también se ve afectado MIENTRAS hay inferencia en
    # curso (el server de 1 slot puede tardar en responder health bajo carga).
    # Esto ejercita el log de PAUSA del código nuevo de forma fiel a su lógica.
    import httpx
    _RealAsyncClient = httpx.AsyncClient
    class _HealthFailDuringInferenceClient:
        def __init__(self, *a, **k):
            self._real = _RealAsyncClient(*a, **k)
        async def __aenter__(self):
            await self._real.__aenter__()
            return self
        async def __aexit__(self, *a):
            return await self._real.__aexit__(*a)
        async def get(self, url, *a, **k):
            if "health" in str(url) and LLM_INFERENCE_IN_PROGRESS.is_set():
                r = MagicMock(); r.status_code = 503
                return r
            return await self._real.get(url, *a, **k)
        async def post(self, url, *a, **k):
            return await self._real.post(url, *a, **k)

    # Acelerar los sleeps del loop para observabilidad (gracia + ciclos 8s)
    real_sleep = asyncio.sleep
    async def fast_sleep(s):
        if s >= 30:
            await real_sleep(8.0)   # gracia 8s
        else:
            await real_sleep(0.05)  # ciclos casi inmediatos tras gracia

    # Lanzar el loop nuevo en segundo plano
    async def loop_wrapper():
        with patch.object(asyncio, "sleep", side_effect=fast_sleep), \
             patch("httpx.AsyncClient", _HealthFailDuringInferenceClient):
            await m.llama_self_healing_loop()

    task = asyncio.create_task(loop_wrapper())

    # Esperar la "gracia" de 8s + un ciclo para que el loop arranque
    await real_sleep(10.0)
    logger.info("Loop self-healing nuevo CORRIENDO. Lanzando inferencia local larga...")

    # Inferencia local REAL y larga (n_predict=512, prompt CORTO para no
    # exceder el contexto de 4096 tokens; la generación de 512 tokens tarda ~40s)
    prompt = (
        "Eres un experto en impacto ambiental. Clasifica el siguiente proyecto "
        "como FAVORABLE, CONDICIONADO o DESFAVORABLE y explica brevemente por qué. "
        "Proyecto: construcción de un puente vehicular de 200m sobre el río Grijalva "
        "en Chiapas, con estudios de fauna acuática pendientes."
    )
    t0 = time.time()
    try:
        result = _complete_local(
            prompt=prompt, system_prompt=None, response_json=True,
            n_predict=512, prefer_local=True,
        )
        dt = time.time() - t0
        logger.info("INFERENCIA LOCAL COMPLETÓ en %.1fs", dt)
        logger.info("RESULT modelo: %s", result.get("meta", {}).get("modelo"))
        logger.info("RESULT content head: %s", str(result)[:200])
    except Exception as exc:
        dt = time.time() - t0
        logger.error("INFERENCIA LOCAL FALLÓ tras %.1fs: %s", dt, exc)
        result = None

    # Dejar correr el loop un poco más para que procese el fin de la inferencia
    await real_sleep(12.0)

    logger.info("=== RESULTADO E2E ===")
    logger.info("restart_llama_container (mock) llamado: %d (esperado 0)", restart_calls["n"])
    logger.info("bandera LLM_INFERENCE_IN_PROGRESS al final: %s", LLM_INFERENCE_IN_PROGRESS.is_set())
    logger.info("tiempo inferencia: %.1fs", dt if 'dt' in dir() else -1)
    if result is not None:
        logger.info("inferencia exitosa y LOCAL: %s", result.get("meta", {}).get("modelo"))
    task.cancel()

if __name__ == "__main__":
    asyncio.run(run())

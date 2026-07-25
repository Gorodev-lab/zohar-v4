"""Prueba aislada de la lógica de PAUSA del self-healing nuevo.

El server real de llama.cpp se autotermina ~40s (bug de binario, opción D
pendiente), por lo que no puede sostener una inferencia de 40s+ para acumular
3 ciclos unhealthy CON la bandera SET. Aquí simulamos una inferencia estable
de 40s (mock de _complete_local) y forzamos /health=503 durante ese tiempo,
para confirmar que el loop nuevo ACUMULA 3 ciclos y PAUSA el restart porque
LLM_INFERENCE_IN_PROGRESS está activo (log 'HAY inferencia real en curso').
"""
import asyncio
import logging
import sys
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("pause_test")

import httpx
import api.main as m
from core.llm_client import LLM_INFERENCE_IN_PROGRESS

restart_calls = {"n": 0}
async def fake_restart_llama_container():
    restart_calls["n"] += 1
    logger.warning(">>> restart_llama_container() LLAMADO (mock) n=%d", restart_calls["n"])

def _simulated_inference_thread():
    """Inferencia simulada estable de 40s en un hilo (no afectada por el
    patch de asyncio.sleep del loop). SET/CLEAR la bandera."""
    LLM_INFERENCE_IN_PROGRESS.set()
    try:
        time.sleep(40.0)
    finally:
        LLM_INFERENCE_IN_PROGRESS.clear()

async def run():
    m.restart_llama_container = fake_restart_llama_container

    # Health simulado caído (503) MIENTRAS la bandera está SET
    _Real = httpx.AsyncClient
    class _H:
        def __init__(self, *a, **k): self._r = _Real(*a, **k)
        async def __aenter__(self): await self._r.__aenter__(); return self
        async def __aexit__(self, *a): return await self._r.__aexit__(*a)
        async def get(self, url, *a, **k):
            if "health" in str(url) and LLM_INFERENCE_IN_PROGRESS.is_set():
                r = MagicMock(); r.status_code = 503; return r
            return await self._r.get(url, *a, **k)
        async def post(self, url, *a, **k):
            return await self._r.post(url, *a, **k)

    real_sleep = asyncio.sleep
    async def fast_sleep(s):
        # Cualquier sleep largo del loop -> 0.1s para acelerar ciclos de prueba
        if s >= 1.0:
            await real_sleep(0.1)
        else:
            await real_sleep(0.01)

    async def loop_wrapper():
        with patch.object(asyncio, "sleep", side_effect=fast_sleep), \
             patch("httpx.AsyncClient", _H):
            await m.llama_self_healing_loop()

    task = asyncio.create_task(loop_wrapper())
    await real_sleep(0.3)
    logger.info("Loop CORRIENDO. Lanzando inferencia simulada estable de 40s (hilo)...")
    # Inferencia simulada en hilo aparte (time.sleep, no afectada por parche async)
    inf_thread = threading.Thread(target=_simulated_inference_thread, daemon=True)
    inf_thread.start()
    await real_sleep(3.0)  # dejar que el loop haga >=3 ciclos unhealthy con bandera SET
    logger.info("Inferencia simulada en curso ~3s (bandera SET). Esperando a que termine...")
    inf_thread.join(timeout=45)
    logger.info("=== RESULTADO PAUSA ===")
    logger.info("restart_llama_container (mock) llamado: %d (esperado 0)", restart_calls["n"])
    logger.info("bandera al final: %s", LLM_INFERENCE_IN_PROGRESS.is_set())
    task.cancel()


if __name__ == "__main__":
    asyncio.run(run())

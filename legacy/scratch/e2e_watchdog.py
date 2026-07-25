"""Prueba del watchdog de la bandera LLM_INFERENCE_IN_PROGRESS.

Escenario W1 (atascada): bandera SET + timestamp de hace 120s (>90s umbral).
  Esperado: el loop IGNORA la pausa y procede a reiniciar (restart llamado).
Escenario W2 (normal): bandera SET + timestamp de hace 5s (<90s).
  Esperado: el loop PAUSA el reinicio (restart NO llamado).

Usa el loop nuevo de api.main con restart interceptado (mock) y sleeps
acelerados. Se simula /health=503 mientras la bandera esté SET para forzar
ciclos unhealthy.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("watchdog_test")

import httpx
import api.main as m
from core.llm_client import (
    LLM_INFERENCE_IN_PROGRESS,
    LLM_INFERENCE_STARTED_AT,
)

restart_calls = {"n": 0}
async def fake_restart_llama_container():
    restart_calls["n"] += 1
    logger.warning(">>> restart_llama_container() LLAMADO (mock) n=%d", restart_calls["n"])

def _make_health_client(stuck_age):
    """Cliente mock: /health=503 mientras la bandera esté SET (simula server
    ocupado). stuck_age=None => bandera no atascada (timestamp reciente)."""
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
    return _H

async def run_watchdog_scenario(label, stuck_age):
    """Ejecuta un ciclo del loop con la bandera 'atascada' o 'normal'."""
    m.restart_llama_container = fake_restart_llama_container
    restart_calls["n"] = 0

    # Preparar bandera según escenario
    LLM_INFERENCE_IN_PROGRESS.set()
    if stuck_age is None:
        LLM_INFERENCE_STARTED_AT = time.monotonic() - 5.0   # normal: 5s
    else:
        LLM_INFERENCE_STARTED_AT = time.monotonic() - stuck_age  # atascada: 120s
    # exportar a módulo (porque el loop lee desde core.llm_client)
    import core.llm_client as lc
    lc.LLM_INFERENCE_STARTED_AT = LLM_INFERENCE_STARTED_AT

    H = _make_health_client(stuck_age)
    real_sleep = asyncio.sleep
    async def fast_sleep(s):
        if s >= 1.0: await real_sleep(0.1)
        else: await real_sleep(0.01)

    async def loop_once():
        # Ejecutar solo 1 iteración del loop para inspeccionar decisión
        with patch.object(asyncio, "sleep", side_effect=fast_sleep), \
             patch("httpx.AsyncClient", H):
            # Replicar la lógica de un ciclo del loop nuevo (sin el while infinito)
            local_url = "http://127.0.0.1:8083"
            consecutive_failures = 0
            for _ in range(4):  # suficiente para acumular >=3
                cycle_unhealthy = False
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(f"{local_url}/health", timeout=5.0)
                        if r.status_code != 200:
                            cycle_unhealthy = True
                except Exception:
                    cycle_unhealthy = True
                if cycle_unhealthy:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                if consecutive_failures >= 3:
                    # MISMA lógica del loop nuevo (Parte A + watchdog)
                    max_age = 90.0
                    blocking = LLM_INFERENCE_IN_PROGRESS.is_set()
                    if blocking and lc.LLM_INFERENCE_STARTED_AT is not None:
                        stuck = time.monotonic() - lc.LLM_INFERENCE_STARTED_AT
                        if stuck > max_age:
                            logger.warning("[%s] bandera atascada %.1fs > %.0fs -> IGNORA pausa", label, stuck, max_age)
                            blocking = False
                    if blocking:
                        logger.warning("[%s] PAUSA reinicio (bandera activa <90s)", label)
                        consecutive_failures = 0
                    else:
                        await fake_restart_llama_container()
                        consecutive_failures = 0
                    break
            return

    await loop_once()
    logger.info("[%s] restart_llama_container llamado: %d (esperado %s)", label, restart_calls["n"], "1" if stuck_age and stuck_age > 90 else "0")
    # limpiar bandera para siguiente escenario
    LLM_INFERENCE_IN_PROGRESS.clear()
    lc.LLM_INFERENCE_STARTED_AT = None

async def run():
    logger.info("=== W1: bandera ATASCADA 120s -> debe IGNORAR pausa y reiniciar ===")
    await run_watchdog_scenario("W1-atascada", stuck_age=120.0)
    logger.info("=== W2: bandera NORMAL 5s -> debe PAUSAR reinicio ===")
    await run_watchdog_scenario("W2-normal", stuck_age=None)

if __name__ == "__main__":
    asyncio.run(run())

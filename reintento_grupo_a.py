#!/usr/bin/env python3
"""
reintento_grupo_a.py - Reintento de inferencia LLM (RSI) sobre el grupo (a) de fallidas.

Cumple el protocolo del usuario:
  - Solo procesa las claves de .reintento_a.txt (grupo a: 108 claves con PDF valido)
  - Cap de 45 iteraciones de polling por documento (mismo patron que run_dgira_batch_01.py)
  - Si el reintento pasa por LLM local (httpx -> API 8004 -> llama_cpp), vigila
    EXPLICITAMENTE httpx.RemoteProtocolError (deuda tecnica "Option D") y logea
    cada ocurrencia por separado, sin confundirla con fallo de health-check.

NOTA: este script es para la fase de INFERENCIA (RSI/LLM). La extraccion OCR (FASE A)
ya corre en paralelo via extraer_corpus_faltante.py --checkpoint por_hacer.txt.
"""
from __future__ import annotations
import asyncio, json, logging, httpx, re
from pathlib import Path

BASE = Path(__file__).resolve().parent
API_URL = "http://127.0.0.1:8004"
CHECKPOINT = BASE / ".reintento_a.txt"
LOG_PATH = BASE / "logs" / "reintento_a_llm.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("Reintento-A")

MAX_POLL = 45          # cap de 45 intentos (180s a 4s)
POLL_SLEEP = 4.0
remote_protocol_errors = 0   # contador explicito Option D

def cargar_claves() -> list[str]:
    if not CHECKPOINT.exists():
        return []
    return [l.strip() for l in CHECKPOINT.read_text().splitlines() if l.strip()]

async def procesar_clave(clave: str):
    """Dispara inferencia RSI para la clave y hace polling con cap 45."""
    global remote_protocol_errors
    md_candidato = next(BASE.glob(f"extractions/{clave}*.md"), None)
    if not md_candidato:
        logger.warning("[%s] sin .md de extraccion todavia; se omite inferencia", clave)
        return "SIN_MD"
    payload = {"doc_id": md_candidato.name, "task": "Extraer entidades y relaciones para el grafo"}
    try:
        res = httpx.post(f"{API_URL}/api/rsi/run", json=payload, timeout=15.0)
        if res.status_code != 200:
            logger.error("[%s] HTTP %s al iniciar RSI", clave, res.status_code)
            return "HTTP_ERR"
        job_id = res.json().get("job_id")
        for intento in range(1, MAX_POLL + 1):
            try:
                st = httpx.get(f"{API_URL}/api/rsi/status/{job_id}", timeout=15.0).json()
                status = st.get("status", "UNKNOWN")
                if status in ("COMPLETED", "FAILED", "PERSISTED"):
                    logger.info("[%s] RSI %s (intento %d)", clave, status, intento)
                    return status
            except httpx.RemoteProtocolError as e:
                # Deuda tecnica "Option D": NO es fallo de health-check.
                remote_protocol_errors += 1
                logger.error("[%s] RemoteProtocolError (Option D) intento %d: %s", clave, intento, e)
            await asyncio.sleep(POLL_SLEEP)
        logger.warning("[%s] timeout de polling tras %d intentos", clave, MAX_POLL)
        return "TIMEOUT_POLL"
    except httpx.RemoteProtocolError as e:
        remote_protocol_errors += 1
        logger.error("[%s] RemoteProtocolError (Option D) en POST: %s", clave, e)
        return "REMOTE_PROTO"
    except Exception as e:
        logger.error("[%s] excepcion: %s", clave, e)
        return "EXC"

async def main():
    claves = cargar_claves()
    logger.info("Reintento grupo (a): %d claves | cap polling=%d", len(claves), MAX_POLL)
    resultados = {}
    for c in claves:
        resultados[c] = await procesar_clave(c)
    rec = sum(1 for v in resultados.values() if v in ("COMPLETED", "PERSISTED"))
    logger.info("=" * 50)
    logger.info("RECUPERADAS (LLM): %d/%d", rec, len(claves))
    logger.info("RemoteProtocolError capturados (Option D): %d", remote_protocol_errors)
    logger.info("=" * 50)
    # volcar resumen
    (BASE / "logs" / "reintento_a_resumen.json").write_text(
        json.dumps({"recuperadas": rec, "total": len(claves),
                    "remote_protocol_errors": remote_protocol_errors,
                    "detalle": resultados}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())

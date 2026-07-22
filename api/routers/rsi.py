"""
api/routers/rsi.py
==================
Endpoints de control para Auto-Mejora Recursiva (RSI) y Curaduría Atómica en segundo plano.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from core.config import PID_FILE as RSI_PID_FILE, LOG_FILE as RSI_LOG_FILE, PYTHON_EXE as RSI_PYTHON_EXE, PROJECT_ROOT as RSI_PROJECT_ROOT

logger = logging.getLogger("api_rsi")

router = APIRouter(tags=["rsi"])

ATOMIC_RSI_ACTIVE = False
_atomic_rsi_task = None

def _sse_response(generator):
    async def event_publisher():
        async for item in generator:
            payload = json.dumps(item, ensure_ascii=False)
            yield f"data: {payload}\n\n"
    return StreamingResponse(event_publisher(), media_type="text/event-stream")

def _rsi_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _atomic_rsi_worker_loop():
    """Background worker que ejecuta 1 iteración atómica de curaduría cada 30 segundos."""
    global ATOMIC_RSI_ACTIVE
    from core.rsi_brain import run_atomic_metadata_curation_step
    from core.broadcaster import broadcaster
    logger.info("Iniciando background worker de RSI Auto-Curaduría Atómica...")
    while ATOMIC_RSI_ACTIVE:
        try:
            res = await asyncio.to_thread(run_atomic_metadata_curation_step)
            if res.get("curated"):
                logger.info("RSI Atómico curó ficha %s: %s", res.get("clave"), res.get("metadata"))
                broadcaster.broadcast("atomic_curation_updated", res)
        except Exception as exc:
            logger.warning("Error en worker RSI atómico: %s", exc)
        await asyncio.sleep(30)


@router.get("/api/rsi/status")
def rsi_status():
    if RSI_PID_FILE.exists():
        try:
            pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
            if _rsi_is_running(pid):
                return {"running": True, "pid": pid}
        except (ValueError, OSError):
            pass
        RSI_PID_FILE.unlink(missing_ok=True)
    return {"running": False}


@router.post("/api/rsi/start")
def rsi_start(iterations: int = 2):
    if RSI_PID_FILE.exists():
        try:
            pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
            if _rsi_is_running(pid):
                return {"status": "already_running", "pid": pid}
        except (ValueError, OSError):
            pass
    RSI_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(RSI_LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [RSI_PYTHON_EXE, "rsi_run_all.py", "--cycles-per-target", str(iterations)],
        cwd=str(RSI_PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    RSI_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return {"status": "started", "pid": proc.pid}


@router.post("/api/rsi/stop")
def rsi_stop():
    if not RSI_PID_FILE.exists():
        return {"status": "not_running"}
    try:
        pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, ValueError, OSError):
        pass
    RSI_PID_FILE.unlink(missing_ok=True)
    return {"status": "stopped"}


@router.get("/api/rsi/run")
def run_rsi_endpoint(cycles: int = Query(3, ge=1, le=10), dry_run: bool = Query(False)):
    """
    Ejecuta el ciclo de Auto-Mejora Recursiva (RSI) sobre semarnat_downloader.py
    y emite eventos SSE en tiempo real para el Dashboard.
    """
    from auto_improver import run_rsi_stream
    gen = run_rsi_stream(max_cycles=cycles, dry_run=dry_run)
    return _sse_response(gen)


@router.get("/api/rsi/toggle-status")
def get_atomic_rsi_toggle_status():
    """Retorna el estado activo/inactivo del Toggle de RSI Auto-Curaduría Atómica."""
    global ATOMIC_RSI_ACTIVE
    return {"active": ATOMIC_RSI_ACTIVE}


@router.post("/api/rsi/toggle")
async def toggle_atomic_rsi(payload: dict):
    """Activa o desactiva el Toggle de RSI Auto-Curaduría Atómica desde el Dashboard UI."""
    global ATOMIC_RSI_ACTIVE, _atomic_rsi_task
    enable = payload.get("enable", not ATOMIC_RSI_ACTIVE)
    ATOMIC_RSI_ACTIVE = enable

    if ATOMIC_RSI_ACTIVE:
        if _atomic_rsi_task is None or _atomic_rsi_task.done():
            _atomic_rsi_task = asyncio.create_task(_atomic_rsi_worker_loop())
        msg = "RSI Auto-Curaduría Atómica ACTIVADA"
    else:
        if _atomic_rsi_task and not _atomic_rsi_task.done():
            _atomic_rsi_task.cancel()
        msg = "RSI Auto-Curaduría Atómica DESACTIVADA"

    logger.info(msg)
    return {"active": ATOMIC_RSI_ACTIVE, "msg": msg}

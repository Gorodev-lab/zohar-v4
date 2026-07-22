"""
api/routers/enricher.py
========================
Endpoints de control y monitoreo para BackgroundEnricherWatcher.
"""

import logging
from fastapi import APIRouter, Query
from core.llm_enricher import enricher_watcher

logger = logging.getLogger("api_enricher")

router = APIRouter(prefix="/api/enricher", tags=["enricher"])


@router.get("/status")
def get_enricher_status():
    """Devuelve el estado operativo, métricas y cola pendiente de BackgroundEnricherWatcher."""
    return enricher_watcher.get_status()


@router.post("/start")
def start_enricher():
    """Inicia el servicio BackgroundEnricherWatcher en segundo plano."""
    if enricher_watcher._running:
        return {"status": "already_running", "msg": "BackgroundEnricherWatcher ya se encuentra en ejecución."}
    
    enricher_watcher.start()
    return {"status": "started", "msg": "BackgroundEnricherWatcher iniciado con éxito."}


@router.post("/stop")
def stop_enricher():
    """Detiene el servicio BackgroundEnricherWatcher."""
    if not enricher_watcher._running:
        return {"status": "not_running", "msg": "BackgroundEnricherWatcher no está activo."}

    enricher_watcher.stop()
    return {"status": "stopped", "msg": "BackgroundEnricherWatcher detenido con éxito."}


@router.post("/trigger")
def trigger_enrichment(limit: int = Query(5, ge=1, le=50)):
    """Ejecuta inmediatamente un ciclo manual de enriquecimiento en hasta N proyectos pendientes."""
    res = enricher_watcher.trigger_cycle(limit=limit)
    return {
        "status": "success",
        "msg": f"Ciclo manual ejecutado. Procesados: {res['processed_count']}",
        "data": res
    }

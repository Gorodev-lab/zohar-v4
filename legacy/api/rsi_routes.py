"""
api/rsi_routes.py
=================
Rutas de API FastAPI para la ejecución de tareas de RLM y el RSI_LOOP.
"""

from __future__ import annotations

import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from core.rlm_harness import RLMHarness
from core.rsi_orchestrator import RSILoopOrchestrator
from core.subagents.graph_extractor import GraphExtractor
from core.config import PROJECT_ROOT, EXTRACTIONS_DIR, DOWNLOADS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rsi", tags=["rsi_loop"])

# Almacenamiento en memoria para el estado de los trabajos en segundo plano
RSI_JOBS: Dict[str, Dict[str, Any]] = {}


class RSIRunRequest(BaseModel):
    doc_id: str
    task: str = "extraer grafo"


def _find_document_text(doc_id: str) -> str:
    """
    Busca el texto correspondiente a un doc_id en las rutas estándar de Zohar v4.
    """
    # Limpiar posibles extensiones del input
    clean_id = Path(doc_id).stem

    candidates = [
        EXTRACTIONS_DIR / f"{clean_id}.md",
        EXTRACTIONS_DIR / f"{clean_id}.estudio.00.md",
        EXTRACTIONS_DIR / f"{clean_id}.resumen.00.md",
        EXTRACTIONS_DIR / f"{clean_id}.resolutivo.00.md",
        EXTRACTIONS_DIR / doc_id,  # Si pasaron nombre de archivo completo
        PROJECT_ROOT / "second_brain" / "01_Sources" / f"{clean_id}.md",
        PROJECT_ROOT / "second_brain" / "01_Sources" / f"{clean_id}.estudio.00.md",
        PROJECT_ROOT / "second_brain" / "01_Sources" / f"{clean_id}.resumen.00.md",
    ]

    for cand in candidates:
        if cand.exists() and cand.is_file():
            try:
                content = cand.read_text(encoding="utf-8", errors="ignore").strip()
                if content:
                    logger.info(f"Rutas RSI: Encontrado archivo de texto en {cand}")
                    return content
            except Exception as e:
                logger.warning(f"Error leyendo candidato {cand}: {e}")

    # Fallback: buscar cualquier archivo que comience con clean_id en extractions/
    if EXTRACTIONS_DIR.exists():
        for f in EXTRACTIONS_DIR.glob(f"{clean_id}*"):
            if f.is_file():
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").strip()
                    if content:
                        logger.info(f"Rutas RSI: Encontrado archivo fallback en {f}")
                        return content
                except Exception:
                    pass

    raise FileNotFoundError(f"No se pudo encontrar ningún archivo de texto para doc_id: {doc_id}")


def _execute_loop_job(job_id: str, doc_id: str, task_description: str):
    """
    Función de ejecución en segundo plano para el RSILoopOrchestrator.
    """
    logger.info(f"Iniciando Job {job_id} para doc_id={doc_id}, tarea='{task_description}'")
    RSI_JOBS[job_id]["status"] = "IN_PROGRESS"

    try:
        # 1. Cargar el texto del documento
        raw_text = _find_document_text(doc_id)
        
        # 2. Inicializar RLMHarness y descargar el texto
        harness = RLMHarness(use_redis_if_available=True)
        symbolic_tag = harness.offload_text(raw_text, var_name="[VAR_DOC_01]")

        # 3. Configurar orquestador y registrar sub-agentes
        orchestrator = RSILoopOrchestrator(harness, max_iterations=5)
        extractor = GraphExtractor(harness)
        orchestrator.register_subagent("graph_extractor", extractor.extract_graph)

        # 4. Preparar variables iniciales
        initial_vars = {
            "[VAR_DOC_01]": symbolic_tag
        }

        # 5. Ejecutar la tarea principal del loop
        result = orchestrator.run_task(
            task_description=f"{task_description} sobre [VAR_DOC_01]",
            initial_variables=initial_vars
        )

        # 6. Actualizar estado del Job
        RSI_JOBS[job_id]["status"] = result["status"]
        RSI_JOBS[job_id]["history"] = result["history"]
        RSI_JOBS[job_id]["final_summary"] = result["final_summary"]
        RSI_JOBS[job_id]["variables"] = {
            k: (v if not isinstance(v, dict) else {sk: sv for sk, sv in v.items() if sk != "text"})
            for k, v in result["variables"].items()
        }  # Evitamos guardar textos pesados en el log del Job

        logger.info(f"Job {job_id} finalizado exitosamente con estado {result['status']}")

    except Exception as e:
        logger.exception(f"Error ejecutando Job {job_id}")
        RSI_JOBS[job_id]["status"] = "FAILED"
        RSI_JOBS[job_id]["error"] = str(e)


@router.post("/run")
async def run_rsi_loop(request: RSIRunRequest, background_tasks: BackgroundTasks):
    """
    Inicia un trabajo en segundo plano para ejecutar el RSILoopOrchestrator
    sobre el documento especificado.
    """
    # Verificar que el documento exista antes de lanzar el background task
    try:
        _find_document_text(request.doc_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    job_id = str(uuid.uuid4())
    RSI_JOBS[job_id] = {
        "job_id": job_id,
        "doc_id": request.doc_id,
        "task": request.task,
        "status": "QUEUED",
        "history": [],
        "final_summary": "",
        "error": None
    }

    background_tasks.add_task(_execute_loop_job, job_id, request.doc_id, request.task)
    return {"job_id": job_id, "status": "QUEUED"}


@router.get("/status/{job_id}")
async def get_rsi_job_status(job_id: str):
    """
    Retorna el estado de ejecución y el historial detallado de iteraciones de un Job.
    """
    job = RSI_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No se encontró ningún trabajo con ID: {job_id}")
    return job

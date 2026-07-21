"""
api/routers/dw.py
=================
Endpoints de estado del Data Warehouse, telemetría y ejecutor de pipeline.
"""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, text

from core.config import DATABASE_URL, PROJECT_ROOT

logger = logging.getLogger("api_dw")

router = APIRouter(tags=["dw"])

def _sse_response(generator, event_type="update"):
    """Función auxiliar para formatear Server-Sent Events (SSE)."""
    async def event_publisher():
        async for item in generator:
            payload = json.dumps(item, ensure_ascii=False)
            yield f"event: {event_type}\ndata: {payload}\n\n"
    return StreamingResponse(event_publisher(), media_type="text/event-stream")


@router.get("/api/dw/db-status")
async def api_dw_db_status():
    """
    Endpoint de diagnóstico de salud de la base de datos PostgreSQL del Data Warehouse.
    Retorna el estado de conexión y el informe de calidad.
    """
    t_start = time.time()
    db_status = {
        "connected": False,
        "latency_ms": 0,
        "tables": {}
    }

    db_url = DATABASE_URL
    try:
        engine = create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            db_status["latency_ms"] = int((time.time() - t_start) * 1000)
            db_status["connected"] = True
            
            for table in ["semarnat_projects", "project_evaluations"]:
                try:
                    res = conn.execute(text(f"SELECT COUNT(*) FROM public.{table}"))
                    db_status["tables"][table] = {"count": res.scalar()}
                except Exception as tbl_exc:
                    db_status["tables"][table] = {"count": 0, "error": str(tbl_exc)}
    except Exception as exc:
        db_status["error"] = str(exc)

    json_path = PROJECT_ROOT / "dw" / "audit_report.json"
    audit_report = {}
    if json_path.exists():
        try:
            audit_report = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as err:
            logger.error("Error leyendo audit_report.json: %s", err)

    return {
        "db": db_status,
        "quality": audit_report
    }


@router.get("/api/dw/pipeline-stats")
async def get_dw_pipeline_stats():
    """Retorna las estadísticas en tiempo real de la base de datos PostgreSQL."""
    from core.dw_pipeline import get_db_stats
    return get_db_stats()


@router.get("/api/dw/run-pipeline")
async def run_dw_pipeline():
    """
    SSE: Ejecuta el pipeline completo del Data Warehouse y transmite el progreso en tiempo real.
    """
    python_exe = sys.executable

    async def gen():
        yield {"status": "progress", "msg": "Iniciando Data Warehouse Pipeline...", "pct": 5, "stage": "init"}

        pipeline_path = PROJECT_ROOT / "dw" / "pipeline.py"
        if not pipeline_path.exists():
            yield {"status": "error", "msg": "Script dw/pipeline.py no encontrado", "pct": 100}
            return

        cmd = [python_exe, str(pipeline_path)]
        logger.info("Iniciando pipeline DW: %s", " ".join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(PROJECT_ROOT)
            )

            stages_map = [
                ("=== LOGR DATA WAREHOUSE PIPELINE RUN ===", 8, "init", "Iniciando Data Warehouse Pipeline..."),
                ("[Schema] Executing schema.sql...", 15, "schema", "Inicializando esquema de base de datos..."),
                ("[Claves] Loading target environmental claves...", 30, "claves", "Cargando claves de interés..."),
                ("[SEMARNAT] Querying portal and downloading missing files...", 45, "semarnat", "Consultando portal SEMARNAT..."),
                ("[Markdown] Converting study PDFs to Markdown...", 60, "markdown", "Extrayendo texto Markdown de estudios..."),
                ("[Inferencia] Running AI environmental viability evaluations...", 75, "inference", "Evaluando viabilidad ambiental (Gemini/IA)..."),
                ("[Auditor] Gathering data and running Quality Auditor...", 85, "auditor", "Ejecutando auditoría de calidad de datos..."),
                ("[Ingest] Ingesting audited records into database...", 92, "ingest", "Ingiriendo registros limpios en PostgreSQL..."),
                ("[Second Brain] Sincronizando notas del Second Brain...", 97, "second_brain", "Sincronizando notas del Second Brain..."),
                ("=== PIPELINE RUN COMPLETED SUCCESSFULLY ===", 100, "done", "¡Pipeline completado con éxito!"),
            ]

            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                
                line_clean = line.strip()
                if not line_clean:
                    continue
                
                event = {"status": "log", "msg": line_clean}
                
                for pattern, pct, stage, friendly_msg in stages_map:
                    if pattern in line_clean:
                        event["status"] = "progress"
                        event["pct"] = pct
                        event["stage"] = stage
                        event["msg"] = friendly_msg
                        break
                
                yield event
            
            proc.wait()
            if proc.returncode == 0:
                yield {"status": "complete", "msg": "Pipeline completado con éxito", "pct": 100}
            else:
                yield {"status": "error", "msg": f"El pipeline falló con código de salida {proc.returncode}", "pct": 100}
        
        except Exception as exc:
            logger.error("Error ejecutando pipeline: %s", exc)
            yield {"status": "error", "msg": f"Error: {exc}", "pct": 100}

    return _sse_response(gen(), "dw_pipeline")

"""
api/routers/extraction.py
==========================
Endpoints para extracción estructurada con LLM, inferencia de viabilidad ambiental y batch summaries.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.config import PROJECT_ROOT

logger = logging.getLogger("api_extraction")

router = APIRouter(tags=["extraction"])

def _sse_response(generator):
    async def event_publisher():
        async for item in generator:
            payload = json.dumps(item, ensure_ascii=False)
            yield f"data: {payload}\n\n"
    return StreamingResponse(event_publisher(), media_type="text/event-stream")


@router.post("/api/extract/structured", tags=["extraction"])
def extract_structured_project(payload: dict):
    """
    Endpoint para ejecutar la Extracción Estructurada Avanzada con LLM.
    Persiste los resultados en PostgreSQL (project_evaluations) y en el Vault de Obsidian.
    """
    clave = payload.get("clave")
    if not clave:
        raise HTTPException(status_code=400, detail="Se requiere 'clave'")

    md_file = PROJECT_ROOT / "extractions" / f"{clave}.md"
    if not md_file.exists():
        found = list((PROJECT_ROOT / "extractions").rglob(f"{clave}*.md"))
        if found:
            md_file = found[0]
        else:
            raise HTTPException(status_code=404, detail=f"No se encontró archivo Markdown para la clave {clave}")

    md_content = md_file.read_text(encoding="utf-8", errors="ignore")
    
    from core.structured_extractor import StructuredExtractor
    from core.dw_pipeline import upsert_project_evaluation
    from core.second_brain import SecondBrainBuilder

    extractor = StructuredExtractor()
    evaluation = extractor.extract_from_markdown(clave, md_content)
    eval_dict = evaluation.model_dump()

    dw_res = upsert_project_evaluation(eval_dict)
    builder = SecondBrainBuilder(base_dir=PROJECT_ROOT)
    obsidian_updated = builder.update_note_frontmatter(clave, eval_dict)

    return {
        "status": "PASS",
        "clave": clave,
        "evaluation": eval_dict,
        "dw_status": dw_res,
        "obsidian_updated": obsidian_updated
    }


@router.post("/api/extract/batch", tags=["extraction"])
def extract_structured_batch(payload: dict):
    """Ejecuta la extracción estructurada en lote para múltiples proyectos pendientes."""
    limit = payload.get("limit", 5)
    extractions_dir = PROJECT_ROOT / "extractions"
    md_files = list(extractions_dir.glob("*.md"))[:limit]

    results = []
    from core.structured_extractor import StructuredExtractor
    from core.dw_pipeline import upsert_project_evaluation
    from core.second_brain import SecondBrainBuilder

    extractor = StructuredExtractor()
    builder = SecondBrainBuilder(base_dir=PROJECT_ROOT)

    for f in md_files:
        clave = f.stem
        try:
            md_content = f.read_text(encoding="utf-8", errors="ignore")
            evaluation = extractor.extract_from_markdown(clave, md_content)
            eval_dict = evaluation.model_dump()

            dw_res = upsert_project_evaluation(eval_dict)
            obsidian_updated = builder.update_note_frontmatter(clave, eval_dict)

            results.append({
                "clave": clave,
                "status": "PASS",
                "dw_status": dw_res.get("status"),
                "obsidian_updated": obsidian_updated
            })
        except Exception as exc:
            results.append({
                "clave": clave,
                "status": "ERROR",
                "message": str(exc)
            })

    return {"total": len(results), "results": results}


@router.get("/api/corpus/batch-summaries", tags=["corpus"])
def batch_summarize_corpus(max_files: int = Query(5, ge=1, le=50), max_chunks: int = Query(4, ge=1, le=10)):
    """
    SSE stream: Procesa secuencialmente PDFs pendientes generando notas y metadatos en Second Brain + Postgres.
    """
    from core.pdf_summarizer import batch_summarize_unprocessed_pdfs_gen
    gen = batch_summarize_unprocessed_pdfs_gen(max_files=max_files, max_chunks=max_chunks)
    return _sse_response(gen)

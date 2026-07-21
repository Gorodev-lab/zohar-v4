"""
api/routers/rag.py
==================
Endpoints de recuperación híbrida (RAG) y re-indexación semántica de documentos y vault.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from core.config import PROJECT_ROOT

logger = logging.getLogger("api_rag")

router = APIRouter(tags=["rag"])

ACTIVE_JOBS = set()


@router.post("/api/rag/query")
def rag_query_endpoint(payload: dict):
    """
    Ejecuta el pipeline RAG completo:
    Recuperación vectorial Top-K + Filtrado por metadatos + Síntesis LLM con Citas.
    """
    query = payload.get("query")
    if not query:
        raise HTTPException(status_code=400, detail="Se requiere 'query'")

    filters = payload.get("filters", {})
    top_k = payload.get("top_k", 5)

    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=PROJECT_ROOT)
    return engine.query_rag(query, filters=filters, top_k=top_k)


@router.get("/api/rag/search")
def rag_search_endpoint(query: str, clave: Optional[str] = None, top_k: int = 5):
    """Búsqueda semántica vectorial pura de chunks con score de similitud."""
    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=PROJECT_ROOT)
    filters = {"clave": clave} if clave else None
    return {"query": query, "chunks": engine.retrieve_context(query, filters=filters, top_k=top_k)}


@router.post("/api/rag/reindex")
def rag_reindex_endpoint(payload: dict = None):
    """Indexa masivamente los documentos Markdown en extractions/ para el motor RAG."""
    limit = (payload or {}).get("limit", 50)
    extractions_dir = PROJECT_ROOT / "extractions"
    if not extractions_dir.exists():
        return {"total": 0, "indexed": [], "status": "No extractions found"}

    md_files = list(extractions_dir.glob("*.md"))[:limit]
    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=PROJECT_ROOT)

    results = []
    for f in md_files:
        clave = f.stem
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            res = engine.index_document(clave, content)
            results.append(res)
        except Exception as exc:
            results.append({"clave": clave, "status": "ERROR", "message": str(exc)})

    return {"total": len(results), "indexed": results}


@router.post("/api/rag/reindex-vault")
async def reindex_vault():
    """Indexa masivamente todas las notas del Second Brain para la búsqueda híbrida."""
    from core.rag_engine import RAGEngine
    ACTIVE_JOBS.add("rag_reindex")
    try:
        engine = RAGEngine(base_dir=PROJECT_ROOT)
        res = await asyncio.to_thread(engine.index_vault)
        return res
    finally:
        ACTIVE_JOBS.discard("rag_reindex")

"""
tests/test_rag_engine.py
Pruebas unitarias e integración para el Motor RAG (Phase 6 RAG Engine & Analytics Agent).
"""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from core.rag_engine import RAGEngine, split_markdown_by_headers

client = TestClient(app)


def test_split_markdown_by_headers():
    """Verifica que el chunker respete las secciones H1, H2, H3 de Markdown."""
    md_text = """# Manifiesto de Impacto Ambiental: Proyecto Solar
    
    Texto introductorio sobre el proyecto solar en Aguascalientes.
    
    ## Resumen Ejecutivo
    El proyecto contempla la construcción de un parque fotovoltaico de 100 MW.
    
    ## Medidas de Mitigación
    - Reforestación compensatoria de flora nativa.
    - Programa de conservación de suelo y agua.
    """

    chunks = split_markdown_by_headers(md_text)
    assert len(chunks) >= 2
    titles = [c["section_title"] for c in chunks]
    assert any("Resumen Ejecutivo" in t for t in titles)
    assert any("Medidas de Mitigación" in t for t in titles)


def test_rag_engine_index_and_retrieve(tmp_path):
    """Verifica la indexación y recuperación vectorial por similitud coseno."""
    engine = RAGEngine(base_dir=tmp_path)

    sample_md = """# Proyecto Fotovoltaico San Antonio
    
    ## Ubicación
    Ubicado en San Antonio de Tepezalá, Estado de Aguascalientes.
    
    ## Impacto Ambiental
    Uso de suelo agrícola y alteración de vegetación de matorral.
    """

    idx_res = engine.index_document("01AG2026X9999", sample_md)
    assert idx_res["status"] == "INDEXED"
    assert idx_res["total_chunks"] >= 2

    # Recuperación
    chunks = engine.retrieve_context("Impacto ambiental en vegetación de matorral", top_k=2)
    assert len(chunks) > 0
    assert chunks[0]["score"] > 0.0
    assert "clave" in chunks[0]


def test_rag_query_with_citations(tmp_path):
    """Verifica la generación de respuesta RAG sintetizada con citas explícitas."""
    engine = RAGEngine(base_dir=tmp_path)

    sample_md = """# Proyecto Minero El Portezuelo
    
    ## Mitigación
    Se instalarán trampas de sedimentos y piscinas de decantación para proteger ríos cercanos.
    """
    engine.index_document("06CL2026H0009", sample_md)

    rag_res = engine.query_rag("¿Qué medidas se proponen para proteger los ríos?", top_k=2)
    assert isinstance(rag_res, dict)
    assert "answer" in rag_res
    assert "citations" in rag_res
    assert len(rag_res["citations"]) > 0
    assert any("[06CL2026H0009 |" in c and "Mitigación]" in c for c in rag_res["citations"])


def test_api_rag_endpoints():
    """Verifica los endpoints /api/rag/query, /api/rag/search y /api/rag/reindex."""
    res_search = client.get("/api/rag/search?query=Impacto+ambiental")
    assert res_search.status_code == 200
    assert "chunks" in res_search.json()

    res_query = client.post("/api/rag/query", json={"query": "Medidas de mitigación para flora"})
    assert res_query.status_code == 200
    assert "answer" in res_query.json()

    res_reindex = client.post("/api/rag/reindex", json={"limit": 2})
    assert res_reindex.status_code == 200
    assert "total" in res_reindex.json()


def test_rag_hybrid_retrieval(tmp_path):
    """Verifica la recuperación híbrida BM25 + Vectorial por RRF."""
    engine = RAGEngine(base_dir=tmp_path)
    sample_md = """# Proyecto Hidrocarburos PEMEX
    ## Normativa
    Cumple con la norma NOM-059-SEMARNAT-2010 y protección de manglar costero.
    """
    engine.index_document("21PU2025H0155", sample_md)

    hybrid_res = engine.retrieve_hybrid("NOM-059 manglar", top_k=2)
    assert len(hybrid_res) > 0
    assert hybrid_res[0]["clave"] == "21PU2025H0155"
    assert hybrid_res[0]["score"] > 0.0

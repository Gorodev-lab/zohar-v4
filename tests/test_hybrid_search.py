"""
tests/test_hybrid_search.py
Pruebas para el motor de búsqueda semántica híbrido (BM25 + Vectorial) y Re-ranking.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.semantic_search import SemanticSearchEngine, _compute_sha256, _cosine_similarity


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert _cosine_similarity(v1, v2) == pytest.approx(1.0)

    v3 = [0.0, 1.0, 0.0]
    assert _cosine_similarity(v1, v3) == pytest.approx(0.0)


@patch("core.semantic_search.SemanticSearchEngine._generate_embedding")
def test_build_index_with_chunks(mock_embed, tmp_path):
    """Verifica que build_index cree correctamente chunks y genere embeddings para cada uno."""
    mock_embed.return_value = [0.1] * 128

    # Crear directorio simulado del Second Brain
    sb_dir = tmp_path / "second_brain"
    sb_dir.mkdir()
    
    # Crear una nota de prueba
    note_content = """# Proyecto Hidroeléctrico El Cajón
    
## Ubicación
Ubicado en el cauce del río Grande de Santiago, Nayarit.

## Mitigación
Se protegerán 100 hectáreas de selva baja caducifolia.
"""
    note_path = sb_dir / "proyecto_el_cajon.md"
    note_path.write_text(note_content, encoding="utf-8")

    engine = SemanticSearchEngine(base_dir=tmp_path)
    res = engine.build_index()

    assert res["status"] == "success"
    assert res["indexed"] == 1
    assert "proyecto_el_cajon.md" in engine.cache
    
    note_cache = engine.cache["proyecto_el_cajon.md"]
    assert "chunks" in note_cache
    assert len(note_cache["chunks"]) >= 2
    
    # Verificar estructura del primer chunk
    first_chunk = note_cache["chunks"][0]
    assert "section_title" in first_chunk
    assert "chunk_text" in first_chunk
    assert "embedding" in first_chunk
    assert len(first_chunk["embedding"]) == 128


@patch("core.semantic_search.SemanticSearchEngine._generate_embedding")
@patch("core.llm_client.generate_completion")
def test_hybrid_search_and_rerank(mock_llm, mock_embed, tmp_path):
    """Verifica que search() realice búsqueda híbrida, RRF y invoque el re-ranking de Gemma."""
    mock_embed.return_value = [0.1] * 128
    
    # Mock de la respuesta estructurada de re-ranking de Gemma
    mock_llm.return_value = {
        "scores": [
            {"id": 0, "score": 2.0},  # ID 0 (Ubicación) obtiene puntaje bajo
            {"id": 1, "score": 9.5}   # ID 1 (Mitigación) obtiene puntaje muy alto
        ]
    }

    # Inicializar base de conocimiento de prueba
    sb_dir = tmp_path / "second_brain"
    sb_dir.mkdir()
    
    note_content = """# Proyecto Hidroeléctrico El Cajón
    
## Ubicación
Ubicado en el cauce del río Grande de Santiago, Nayarit.

## Mitigación
Medidas estrictas de protección de fauna y reforestación forestal.
"""
    note_path = sb_dir / "proyecto_el_cajon.md"
    note_path.write_text(note_content, encoding="utf-8")

    engine = SemanticSearchEngine(base_dir=tmp_path)
    engine.build_index()

    # Ejecutar búsqueda híbrida + re-ranking
    results = engine.search("reforestación forestal", limit=5)

    assert len(results) > 0
    # Tras el re-ranking, el chunk de Mitigación (ID 1) debe quedar en primer lugar
    top_result = results[0]
    assert "Mitigación" in top_result["section_title"]
    assert top_result["score"] > 0.0
    assert "vec_sim" in top_result
    assert "bm25" in top_result
    
    # Verificar que generate_completion fue invocado para re-rankear
    assert mock_llm.call_count == 1

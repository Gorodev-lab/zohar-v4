"""
tests/test_master_pipeline.py
Test de integración del Plan Maestro Secuencial de Zohar v4.
Verifica la interacción entre la ingesta, OCR, persistencia, Second Brain, LLM y API.
"""

import os
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from api.main import app
from core.second_brain import SecondBrainBuilder
from core.semantic_search import SemanticSearchEngine
from scrapers.semarnat_downloader import _classify_by_keyword, POSITIONAL_CLASSIFICATION

client = TestClient(app)


def test_classification_rules():
    """Verifica reglas de clasificación posicional y por keywords."""
    assert _classify_by_keyword("estudio_impacto_ambiental.pdf") == "estudio"
    assert _classify_by_keyword("resumen_ejecutivo_mia.pdf") == "resumen"
    assert _classify_by_keyword("resolucion_oficial.pdf") == "resolutivo"

    assert POSITIONAL_CLASSIFICATION[3][0] == "resumen"
    assert POSITIONAL_CLASSIFICATION[3][1] == "estudio"
    assert POSITIONAL_CLASSIFICATION[3][2] == "resolutivo"


def test_second_brain_vault_building(tmp_path):
    """Verifica la compilación del vault de Obsidian en el Second Brain."""
    builder = SecondBrainBuilder(base_dir=tmp_path)
    stats = builder.build_vault()
    assert isinstance(stats, dict)
    assert builder.sb_dir.exists()



def test_semantic_search_engine_initialization(tmp_path):
    """Verifica la inicialización del motor de búsqueda semántica RAG."""
    engine = SemanticSearchEngine(base_dir=tmp_path)
    index = engine.build_index()
    assert isinstance(index, dict)


def test_api_status_and_health_endpoints():
    """Verifica los endpoints principales del API backend."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or "app" in data or "uptime" in data

    model_resp = client.get("/api/model/status")
    assert model_resp.status_code == 200
    model_data = model_resp.json()
    assert "provider" in model_data

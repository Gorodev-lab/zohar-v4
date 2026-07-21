"""
tests/test_atomic_rsi_and_contracts.py
Pruebas unitarias e integración para el Toggle de RSI Atómico y la Matriz de Contratos.
"""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from api.main import app
from core.stage_contracts import validate_dom_extraction, validate_markdown_extraction, validate_pipeline_contract_all
from core.rsi_brain import run_atomic_metadata_curation_step

client = TestClient(app)


def test_stage_contracts_validation(tmp_path):
    """Verifica los validadores por contrato de etapa."""
    # 1. Contrato DOM
    assert validate_dom_extraction({"project_name": "Test MIA"}) is True
    assert validate_dom_extraction({}) is False

    # 2. Contrato Markdown
    dummy_md = tmp_path / "test.md"
    dummy_md.write_text("A" * 100, encoding="utf-8")
    assert validate_markdown_extraction(dummy_md) is True

    short_md = tmp_path / "short.md"
    short_md.write_text("Short", encoding="utf-8")
    assert validate_markdown_extraction(short_md) is False


def test_atomic_rsi_toggle_api():
    """Verifica los endpoints de control del Toggle RSI Atómico."""
    status_res = client.get("/api/rsi/toggle-status")
    assert status_res.status_code == 200
    assert "active" in status_res.json()

    toggle_on = client.post("/api/rsi/toggle", json={"enable": True})
    assert toggle_on.status_code == 200
    assert toggle_on.json().get("active") is True

    toggle_off = client.post("/api/rsi/toggle", json={"enable": False})
    assert toggle_off.status_code == 200
    assert toggle_off.json().get("active") is False


def test_run_atomic_metadata_curation_step():
    """Verifica la ejecución atómica de curaduría de metadatos."""
    res = run_atomic_metadata_curation_step()
    assert isinstance(res, dict)
    assert "status" in res

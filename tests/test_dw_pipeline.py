"""
tests/test_dw_pipeline.py
==========================
Suite de pruebas automatizadas para la ingesta y auditoría de calidad del Data Warehouse:
- DataQualityAuditor (audit_semarnat_projects, audit_project_evaluations)
- GET /api/dw/db-status
- GET /api/dw/pipeline-stats
"""

import pytest
import pandas as pd
from fastapi.testclient import TestClient

from dw.data_quality_auditor import DataQualityAuditor
from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_auditor_valid_projects():
    """Verifica que el auditor procese correctamente proyectos con formato válido."""
    auditor = DataQualityAuditor(report_path="dw/test_audit_report.md")
    
    raw_data = [
        {
            "clave": "03BS2026H0015",
            "project_name": "Proyecto Valido Sonora",
            "status": "Resuelto",
            "sector": "BS",
            "state": "Sonora",
            "year": 2026,
            "files_downloaded": ["estudio", "resumen"],
            "promovente": "Empresa Test"
        }
    ]
    df = pd.DataFrame(raw_data)
    cleaned_df, metrics = auditor.audit_semarnat_projects(df)
    
    assert len(cleaned_df) == 1
    assert metrics["total_rows"] == 1
    assert metrics["rows_removed"] == 0


def test_auditor_invalid_projects():
    """Verifica que el auditor filtre adecuadamente claves corruptas o fuera de rango."""
    auditor = DataQualityAuditor(report_path="dw/test_audit_report.md")
    
    raw_data = [
        {
            "clave": "CLAVE_INVALIDA_999",
            "project_name": "Proyecto Corrupto",
            "status": "En evaluacion",
            "sector": "XX",
            "state": "Estado Inexistente",
            "year": 1800,
            "files_downloaded": [],
            "promovente": "Desconocido"
        }
    ]
    df = pd.DataFrame(raw_data)
    cleaned_df, metrics = auditor.audit_semarnat_projects(df)
    
    assert len(cleaned_df) == 0
    assert metrics["total_rows"] == 1
    assert metrics["rows_removed"] == 1


def test_auditor_evaluations():
    """Verifica la auditoría de dictámenes e inferencias de IA."""
    auditor = DataQualityAuditor(report_path="dw/test_audit_report.md")
    
    raw_evals = [
        {
            "clave": "03BS2026H0015",
            "veredicto": "VIABLE",
            "score": 0.85,
            "confianza_pct": 90
        },
        {
            "clave": "09LP2026X0001",
            "veredicto": "INVALID_VERDICT",
            "score": 1.5,
            "confianza_pct": 150
        }
    ]
    df = pd.DataFrame(raw_evals)
    cleaned_df, metrics = auditor.audit_project_evaluations(df)
    
    assert len(cleaned_df) == 1
    assert cleaned_df.iloc[0]["clave"] == "03BS2026H0015"
    assert metrics["rows_removed"] == 1


def test_dw_db_status_endpoint(client):
    """Verifica que el endpoint /api/dw/db-status responda 200 OK con métricas."""
    res = client.get("/api/dw/db-status")
    assert res.status_code == 200
    data = res.json()
    assert "db" in data
    assert "quality" in data


def test_dw_pipeline_stats_endpoint(client):
    """Verifica que el endpoint /api/dw/pipeline-stats responda 200 OK."""
    res = client.get("/api/dw/pipeline-stats")
    assert res.status_code == 200
    data = res.json()
    assert "total_proyectos" in data
    assert "total_promoventes" in data

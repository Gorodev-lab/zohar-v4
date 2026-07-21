"""
tests/test_structured_extractor.py
Pruebas unitarias e integración para el Extractor Estructurado de Proyectos Zohar v4.
"""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from core.structured_extractor import StructuredExtractor, ProjectEvaluation, EnvironmentalImpact
from core.dw_pipeline import upsert_project_evaluation
from core.second_brain import SecondBrainBuilder

client = TestClient(app)


def test_pydantic_schema_validation():
    """Verifica que el modelo Pydantic valida correctamente los campos requeridos."""
    impact = EnvironmentalImpact(
        category="Flora",
        description="Pérdida de cubierta vegetal",
        severity="ALTA",
        mitigation_measure="Programa de reforestación 3:1"
    )
    assert impact.category == "Flora"
    assert impact.severity == "ALTA"

    eval_data = ProjectEvaluation(
        clave="01AG2026X9999",
        project_name="Parque Fotovoltaico San Antonio",
        promovente="Energías Verdes S.A.",
        summary="Proyecto de parque solar de 100 MW.",
        impacts=[impact],
        mitigations=["Reforestación compensatoria"],
        legal_risk_level="BAJO",
        confidence_score=0.98
    )
    assert eval_data.clave == "01AG2026X9999"
    assert len(eval_data.impacts) == 1
    assert eval_data.impacts[0].severity == "ALTA"


def test_structured_extractor_fallback(tmp_path):
    """Verifica el extractor estructurado y su comportamiento fallback."""
    extractor = StructuredExtractor(use_gemini_fallback=False)
    md_content = """# Manifiesto de Impacto Ambiental: Proyecto Eólico
    
    Clave: 21PU2025H0155
    Promovente: Eólica del Golfo
    
    ## Impactos
    Afección a aves migratorias por colisión con aerogeneradores.
    
    ## Mitigación
    Instalación de sensores de parada automática.
    """
    res = extractor.extract_from_markdown("21PU2025H0155", md_content)
    assert isinstance(res, ProjectEvaluation)
    assert res.clave == "21PU2025H0155"
    assert res.summary is not None


def test_upsert_project_evaluation_database():
    """Verifica el UPSERT de la evaluación estructurada en PostgreSQL/SQLite."""
    eval_dict = {
        "clave": "TEST_CLAVE_999",
        "project_name": "Proyecto de Prueba E2E",
        "promovente": "Promovente Test",
        "summary": "Resumen de prueba para UPSERT.",
        "legal_risk_level": "MEDIO",
        "confidence_score": 0.95,
        "impacts": [{"category": "Agua", "description": "Uso de acuífero local", "severity": "MEDIA", "mitigation_measure": "Reciclaje de agua"}],
        "mitigations": ["Reciclaje de agua de proceso"]
    }

    res = upsert_project_evaluation(eval_dict)
    assert res["status"] in ["SUCCESS", "FALLBACK_OK"]
    assert res["clave"] == "TEST_CLAVE_999"


def test_obsidian_frontmatter_update(tmp_path):
    """Verifica la actualización de Frontmatter YAML en la nota Obsidian del Second Brain."""
    builder = SecondBrainBuilder(base_dir=tmp_path)
    builder.sources_dir.mkdir(parents=True, exist_ok=True)
    
    note_path = builder.sources_dir / "Proyecto - TEST_CLAVE_999.md"
    note_path.write_text("""---
type: source
category: 01_sources
clave: TEST_CLAVE_999
---

# Proyecto de Prueba
""", encoding="utf-8")

    eval_dict = {
        "clave": "TEST_CLAVE_999",
        "legal_risk_level": "BAJO",
        "summary": "Resumen de prueba enriquecido para Obsidian."
    }

    updated = builder.update_note_frontmatter("TEST_CLAVE_999", eval_dict)
    assert updated is True

    new_content = note_path.read_text(encoding="utf-8")
    assert "legal_risk: BAJO" in new_content
    assert "summary:" in new_content


def test_api_structured_extraction_endpoints(tmp_path):
    """Verifica los endpoints /api/extract/structured y /api/extract/batch."""
    extractions_dir = Path("extractions")
    extractions_dir.mkdir(exist_ok=True)
    test_md = extractions_dir / "TEST_CLAVE_999.md"
    test_md.write_text("# MIA Proyecto Test\nClave: TEST_CLAVE_999\nContenido de prueba para extracción estructurada por API LLM.", encoding="utf-8")

    res = client.post("/api/extract/structured", json={"clave": "TEST_CLAVE_999"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PASS"
    assert data["clave"] == "TEST_CLAVE_999"
    assert "evaluation" in data

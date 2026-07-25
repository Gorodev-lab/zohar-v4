"""Smoke test for core/metadata_extractor.py (Bloque 1, sin LLM).

Verifica:
  - locate_metadata_snippets localiza secciones en MIA reales
  - build_extraction_prompt produce JSON-strict prompt sin llamar LLM
  - validate_extraction: caso OK pasa, caso BAD falla con alertas CRITICAL
  - ESTADOS_VALIDOS tiene 32 entidades (lista cerrada)
"""
from pathlib import Path
import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.metadata_extractor import (
    locate_metadata_snippets, build_extraction_prompt,
    validate_extraction, ESTADOS_VALIDOS, CLAVE_RE, SCHEMA_SQL,
)

BASE = Path(__file__).resolve().parent.parent
EXT = BASE / "extractions"


def _doc(nombre):
    p = EXT / nombre
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def test_estados_cerrados():
    assert len(ESTADOS_VALIDOS) == 32, f"esperado 32, got {len(ESTADOS_VALIDOS)}"


def test_locate_snippets_mia_reales():
    docs = ["04CA2026E0011.estudio.00.md", "03BS2024U0025.estudio.00.md",
            "02BC2024E0044.estudio.01.md", "02BC2025E0049.estudio.01.md"]
    for d in docs:
        txt = _doc(d)
        if not txt:
            continue
        snips = locate_metadata_snippets(txt)
        # datos_generales y descripcion deben tener contenido sustantivo
        assert len(snips["datos_generales"]) > 200, f"{d}: DG vacio"
        assert len(snips["descripcion"]) > 200, f"{d}: DESC vacio"
        # descripcion NO debe colapsar en 'Constitucion Politica' (falso positivo corregido)
        assert "constituci" not in snips["descripcion"].lower()[:200], \
            f"{d}: DESCRIPCION cayo en marco juridico"


def test_prompt_es_json_strict_sin_llm():
    snips = {"datos_generales": "PROMOVENTE EMPRESA X", "descripcion": "Proyecto solar"}
    prompt = build_extraction_prompt(snips)
    assert "JSON" in prompt
    assert "campos_faltantes" in prompt
    assert "NUNCA infieras" in prompt or "nunca infieras" in prompt.lower()


def test_validate_ok():
    data = {"clave": "04CA2026E0011", "promovente": "X", "municipio": "Carmen",
            "estado": "Campeche", "localidad": "Y", "descripcion_proyecto": "Z",
            "confianza_extraccion": "alta", "campos_faltantes": []}
    v = validate_extraction("04CA2026E0011", data)
    assert v["ok"] is True
    assert v["requiere_revision"] is False


def test_validate_bad():
    data = {"clave": "BAD", "promovente": None, "municipio": "X",
            "estado": "Atlantis", "localidad": None, "descripcion_proyecto": None,
            "confianza_extraccion": "baja",
            "campos_faltantes": ["promovente", "estado", "localidad", "descripcion_proyecto"]}
    v = validate_extraction("04CA2026E0011", data)
    assert v["ok"] is False
    campos_alerta = {a["campo"] for a in v["alertas"]}
    assert "clave" in campos_alerta
    assert "estado" in campos_alerta
    assert v["requiere_revision"] is True
    # todas las alertas deben ser CRITICAL (no se escribe a tabla)
    assert all(a["nivel"] == "CRITICAL" for a in v["alertas"])


def test_schema_sql_contiene_tabla():
    assert "metadata_proyecto" in SCHEMA_SQL
    assert "clave" in SCHEMA_SQL and "PRIMARY KEY" in SCHEMA_SQL


if __name__ == "__main__":
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("test_metadata_extractor")
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if res.wasSuccessful() else 1)

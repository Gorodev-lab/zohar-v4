"""
tests/test_second_brain.py
Pruebas de unidad e integración para el módulo de Second Brain en Zohar v4.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_vault_dir(tmp_path):
    """Crea un entorno de directorios temporales para simular el corpus y la caché."""
    downloads = tmp_path / "downloads"
    extractions = tmp_path / "extractions"
    data = tmp_path / "data"

    downloads.mkdir()
    extractions.mkdir()
    data.mkdir()

    # Estructura de downloads
    (downloads / "resumenes").mkdir()
    (downloads / "estudios").mkdir()
    (downloads / "resolutivos").mkdir()
    (downloads / "gacetas").mkdir()

    # Crear gacetas y estudios de prueba (PDFs mockeados)
    gaceta_pdf = downloads / "gacetas" / "gaceta_2026_01.pdf"
    gaceta_pdf.write_bytes(b"%PDF-1.4 mock")

    estudio_pdf = downloads / "estudios" / "21PU2025H0155.pdf"
    estudio_pdf.write_bytes(b"%PDF-1.4 mock study")

    resolutivo_pdf = downloads / "resolutivos" / "21PU2025H0155.pdf"
    resolutivo_pdf.write_bytes(b"%PDF-1.4 mock resolutivo")

    # Crear texto extraído (con claves SINAT simulando una gaceta y un estudio)
    gaceta_md = extractions / "gaceta_2026_01.md"
    gaceta_md.write_text("Esta es la gaceta oficial.\nContiene la clave: 21PU2025H0155\nFin.", encoding="utf-8")

    estudio_md = extractions / "21PU2025H0155.md"
    estudio_md.write_text("# 21PU2025H0155\nEstudio de Impacto Ambiental en Puebla.", encoding="utf-8")

    # Crear cache de inferencia
    inference_dir = data / "inference_cache"
    inference_dir.mkdir()
    inference_json = inference_dir / "21PU2025H0155.json"
    inference_json.write_text(json.dumps({
        "veredicto": "FAVORABLE",
        "score": 0.85,
        "yes_signals": ["Señal de prueba positiva"],
        "no_signals": [],
        "knockouts": [],
        "condicionantes": ["Medida de prueba"],
        "confianza_pct": 95,
        "meta": {"modelo": "test-gemini"}
    }), encoding="utf-8")

    # Parchear las rutas en api.main para cuando llamemos al endpoint
    with patch("api.main.BASE_DIR", tmp_path), \
         patch("api.main.DOWNLOADS_DIR", downloads), \
         patch("api.main.EXTRACTIONS_DIR", extractions), \
         patch("api.main.DATA_DIR", data), \
         patch("api.main.RESUMENES_DIR", downloads / "resumenes"), \
         patch("api.main.ESTUDIOS_DIR", downloads / "estudios"), \
         patch("api.main.RESOLUTIVOS_DIR", downloads / "resolutivos"), \
         patch("api.main.GACETAS_DIR", downloads / "gacetas"), \
         patch.dict("os.environ", {"DATABASE_URL": "sqlite:///:memory:"}), \
         patch("core.semantic_search.SemanticSearchEngine._generate_embedding", return_value=[0.1] * 128):
        yield tmp_path


# ===========================================================================
# Pruebas Unitarias
# ===========================================================================

def test_second_brain_builder_vault_generation(temp_vault_dir):
    """Verifica que el builder genera correctamente la estructura de notas y wiki-links."""
    from core.second_brain import SecondBrainBuilder

    builder = SecondBrainBuilder(temp_vault_dir)
    stats = builder.build_vault()

    # Comprobar estadísticas devueltas
    assert stats["total_proyectos"] == 1
    assert stats["total_gacetas"] == 1
    assert stats["total_municipios"] == 1
    assert stats["total_inferencias"] == 1

    # Comprobar existencia de archivos Markdown interconectados
    sb_dir = temp_vault_dir / "second_brain"
    assert (sb_dir / "00_Index.md").exists()
    assert (sb_dir / "01_Sources" / "Gaceta - gaceta_2026_01.md").exists()
    assert (sb_dir / "02_Entities" / "Proyecto - 21PU2025H0155.md").exists()
    assert (sb_dir / "02_Entities" / "Municipio - Puebla.md").exists()
    assert (sb_dir / "03_Inferences" / "Inferencia - 21PU2025H0155.md").exists()

    # Validar enlaces wiki bidireccionales en el Proyecto
    proj_content = (sb_dir / "02_Entities" / "Proyecto - 21PU2025H0155.md").read_text(encoding="utf-8")
    assert "[[Municipio - Puebla]]" in proj_content
    assert "[[Sector - 21]]" in proj_content
    assert "[[Tipo - MIA Particular]]" in proj_content
    assert "[[Gaceta - gaceta_2026_01]]" in proj_content
    assert "[[Inferencia - 21PU2025H0155]]" in proj_content

    # Validar contenido de la Inferencia
    inf_content = (sb_dir / "03_Inferences" / "Inferencia - 21PU2025H0155.md").read_text(encoding="utf-8")
    assert "[[Proyecto - 21PU2025H0155]]" in inf_content
    assert "Veredicto: **FAVORABLE**" in inf_content
    assert "Señal de prueba positiva" in inf_content


def test_second_brain_bidirectional_sync(temp_vault_dir):
    """Verifica que sync_vault_to_database propague cambios de las notas Markdown a la base de datos SQLite."""
    import sqlalchemy as sa
    from core.second_brain import SecondBrainBuilder

    db_url = "sqlite:///" + str(temp_vault_dir / "test_dw.db")
    engine = sa.create_engine(db_url)
    
    # Crear las tablas de prueba en SQLite
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS semarnat_projects (
                clave VARCHAR(50) PRIMARY KEY,
                project_name TEXT,
                status VARCHAR(255),
                sector VARCHAR(255),
                state VARCHAR(255),
                year INT,
                promovente TEXT,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS project_evaluations (
                clave VARCHAR(50) PRIMARY KEY,
                veredicto VARCHAR(50),
                score DOUBLE PRECISION,
                confianza_pct INT
            )
        """))
        # Insertar un proyecto y evaluación inicial
        conn.execute(sa.text("""
            INSERT OR REPLACE INTO semarnat_projects (clave, project_name, status, promovente)
            VALUES ('21PU2025H0155', 'Proyecto Original', 'INGRESADO', 'Promovente Original')
        """))
        conn.execute(sa.text("""
            INSERT OR REPLACE INTO project_evaluations (clave, veredicto, score, confianza_pct)
            VALUES ('21PU2025H0155', 'PENDIENTE', 0.1, 50)
        """))

    # Crear una nota modificada en la bóveda
    sb_dir = temp_vault_dir / "second_brain"
    entities_dir = sb_dir / "02_Entities"
    entities_dir.mkdir(parents=True, exist_ok=True)
    
    proj_note = entities_dir / "Proyecto - 21PU2025H0155.md"
    proj_note.write_text("""---
type: entity
category: proyecto
clave: 21PU2025H0155
---

# Proyecto SEMARNAT: 21PU2025H0155

## [FICHA] Técnica
- **Nombre del Proyecto:** Proyecto Modificado Manualmente
- **Promovente:** Promovente Modificado
- **Estatus de Trámite:** **AUTORIZADO**
- **Clave de Proyecto:** `21PU2025H0155`
- **Estado/Ubicación:** [[Municipio - Puebla]]
- **Año de Registro:** 2025
""", encoding="utf-8")

    # Inferencia note
    inferences_dir = sb_dir / "03_Inferences"
    inferences_dir.mkdir(parents=True, exist_ok=True)
    inf_note = inferences_dir / "Inferencia - 21PU2025H0155.md"
    inf_note.write_text("""---
type: inference
category: dictamen
clave: 21PU2025H0155
veredicto: FAVORABLE
score: 0.95
---

# Dictamen de Inferencia
Veredicto: **FAVORABLE**
Viabilidad Socio-Ambiental (Score): `95.0%`
""", encoding="utf-8")

    # Ejecutar sincronización
    import os
    from unittest.mock import patch
    builder = SecondBrainBuilder(temp_vault_dir)
    
    with patch.dict(os.environ, {"DATABASE_URL": db_url}):
        res = builder.sync_vault_to_database()
        
    assert res["status"] == "ok"
    assert res["projects_updated"] == 1
    assert res["inferences_updated"] == 1

    # Verificar que los datos en la base de datos se hayan actualizado
    with engine.connect() as conn:
        row_proj = conn.execute(sa.text("SELECT project_name, status, promovente FROM semarnat_projects WHERE clave = '21PU2025H0155'")).fetchone()
        assert row_proj[0] == "Proyecto Modificado Manualmente"
        assert row_proj[1] == "AUTORIZADO"
        assert row_proj[2] == "Promovente Modificado"
        
        row_eval = conn.execute(sa.text("SELECT veredicto, score FROM project_evaluations WHERE clave = '21PU2025H0155'")).fetchone()
        assert row_eval[0] == "FAVORABLE"
        assert row_eval[1] == 0.95


# ===========================================================================
# Pruebas de Integración (API Endpoint)
# ===========================================================================

def test_api_second_brain_build_endpoint(temp_vault_dir):
    """Verifica que el endpoint POST /api/second_brain/build ejecuta la sincronización."""
    from api.main import app
    client = TestClient(app)

    resp = client.post("/api/second_brain/build")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "Second Brain" in data["msg"]
    assert data["stats"]["total_proyectos"] == 1
    assert data["stats"]["total_gacetas"] == 1

    # Verificar que el status del sistema refleja los contadores actualizados del Second Brain
    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert "second_brain" in status_data
    assert status_data["second_brain"]["total_notes"] > 0
    assert status_data["second_brain"]["sources"] == 1
    assert status_data["second_brain"]["entities"] == 4  # Proyecto, Municipio - Puebla, Sector - 21, Tipo - MIA Particular
    assert status_data["second_brain"]["inferences"] == 1


def test_api_second_brain_get_notes_endpoints(temp_vault_dir):
    """Verifica el listado de notas y la recuperación individual de notas wiki."""
    from api.main import app
    client = TestClient(app)

    # 1. Sincronizar bóveda primero
    client.post("/api/second_brain/build")

    # 2. Listar notas
    notes_resp = client.get("/api/second_brain/notes")
    assert notes_resp.status_code == 200
    notes_data = notes_resp.json()
    assert "notes" in notes_data
    assert len(notes_data["notes"]) > 0

    # Comprobar que tiene index y nota de proyecto
    titles = [n["title"] for n in notes_data["notes"]]
    assert "00_Index" in titles
    assert "Proyecto - 21PU2025H0155" in titles

    # 3. Obtener una nota específica por su nombre
    note_resp = client.get("/api/second_brain/note?name=Proyecto - 21PU2025H0155")
    assert note_resp.status_code == 200
    note_data = note_resp.json()
    assert note_data["title"] == "Proyecto - 21PU2025H0155"
    assert "[[Municipio - Puebla]]" in note_data["content"]

    # 4. Intentar obtener una nota inexistente (esperado 404)
    bad_resp = client.get("/api/second_brain/note?name=Nota_Ficticia_Inexistente")
    assert bad_resp.status_code == 404


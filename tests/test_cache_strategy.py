"""
tests/test_cache_strategy.py
Pruebas unitarias y de integración para la estrategia de caché reactiva en Zohar v4.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_dirs(tmp_path):
    """Crea directorios temporales y los asocia a la API."""
    downloads = tmp_path / "downloads"
    extractions = tmp_path / "extractions"
    data = tmp_path / "data"

    downloads.mkdir()
    extractions.mkdir()
    data.mkdir()

    # Subdirectorios del corpus
    (downloads / "resumenes").mkdir()
    (downloads / "estudios").mkdir()
    (downloads / "resolutivos").mkdir()
    (downloads / "gacetas").mkdir()

    with patch("api.main.DOWNLOADS_DIR", downloads), \
         patch("api.main.EXTRACTIONS_DIR", extractions), \
         patch("api.main.DATA_DIR", data), \
         patch("api.main.RESUMENES_DIR", downloads / "resumenes"), \
         patch("api.main.ESTUDIOS_DIR", downloads / "estudios"), \
         patch("api.main.RESOLUTIVOS_DIR", downloads / "resolutivos"), \
         patch("api.main.GACETAS_DIR", downloads / "gacetas"), \
         patch("api.main.REDIS_AVAILABLE", False):
        yield {
            "downloads": downloads,
            "extractions": extractions,
            "data": data,
        }


# ===========================================================================
# 1. Tests de Caché del Grafo
# ===========================================================================

def test_graph_cache_reactive_invalidation(temp_dirs):
    """
    Verifica que la caché del grafo se sirve si no hay cambios,
    y se invalida reactivamente si se agrega o modifica un PDF.
    """
    from api.main import app
    client = TestClient(app)

    # 1. Crear un PDF en downloads
    pdf_path = temp_dirs["downloads"] / "gacetas" / "gaceta_1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 mock")

    # Mock de la función build_full_graph de core.graph_builder
    mock_graph = {
        "nodes": [["n1", "project", "Proyecto 1", "#FFB000", 2026, 1, 0]],
        "links": [],
        "metrics": {"n_nodes": 1, "n_links": 0, "n_projects": 1},
        "schema": {}
    }

    with patch("core.graph_builder.build_full_graph", return_value=mock_graph) as mock_build:
        # Primera llamada: Debe construir el grafo y guardar la caché
        resp1 = client.get("/api/graph")
        assert resp1.status_code == 200
        assert resp1.json()["metrics"]["n_nodes"] == 1
        assert mock_build.call_count == 1

        # Segunda llamada inmediata: Debe servir desde la caché (no llamar a build)
        resp2 = client.get("/api/graph")
        assert resp2.status_code == 200
        assert resp2.json()["metrics"]["n_nodes"] == 1
        assert mock_build.call_count == 1  # Sigue en 1

        # 2. Agregar un nuevo PDF con mtime superior
        time.sleep(0.01)  # Asegurar diferencia de tiempo
        pdf_path_2 = temp_dirs["downloads"] / "estudios" / "estudio_2.pdf"
        pdf_path_2.write_bytes(b"%PDF-1.4 mock 2")

        # Tercera llamada: Al haber un nuevo archivo, debe invalidar e invocar la regeneración
        resp3 = client.get("/api/graph")
        assert resp3.status_code == 200
        assert mock_build.call_count == 2  # Se llamó de nuevo


# ===========================================================================
# 2. Tests de Caché de Inferencia
# ===========================================================================

def test_inference_cache_reactive_invalidation(temp_dirs):
    """
    Verifica que el reporte de inferencia se sirve desde caché si el .md de origen no ha cambiado,
    y se regenera si el .md se actualiza (mtime mayor).
    """
    from api.main import app
    client = TestClient(app)

    # 1. Crear el archivo MD de estudio
    md_filename = "estudio_abc.md"
    md_path = temp_dirs["extractions"] / md_filename
    md_path.write_text("# Proyecto ABC\nTexto original", encoding="utf-8")

    mock_report = {
        "veredicto": "FAVORABLE",
        "score": 0.8,
        "yes_signals": ["Señal ok"],
        "no_signals": [],
        "knockouts": [],
        "condicionantes": [],
        "confianza_pct": 90,
        "meta": {"modelo": "mock"}
    }

    with patch("core.inference_engine.generate_report", return_value=mock_report) as mock_generate:
        # Primera llamada: Genera el reporte y crea la caché
        resp1 = client.get(f"/api/inference/{md_filename}")
        assert resp1.status_code == 200
        assert resp1.json()["veredicto"] == "FAVORABLE"
        assert mock_generate.call_count == 1

        # Segunda llamada: Carga de caché (no genera)
        resp2 = client.get(f"/api/inference/{md_filename}")
        assert resp2.status_code == 200
        assert resp2.json()["veredicto"] == "FAVORABLE"
        assert mock_generate.call_count == 1

        # Modificar el MD de origen para forzar expiración de caché
        time.sleep(0.01)
        md_path.write_text("# Proyecto ABC\nTexto modificado con cambios", encoding="utf-8")

        # Tercera llamada: Debe recalcular
        resp3 = client.get(f"/api/inference/{md_filename}")
        assert resp3.status_code == 200
        assert mock_generate.call_count == 2


# ===========================================================================
# 3. Tests de Caché de Extracción SSE (/stream/single)
# ===========================================================================

def test_extraction_sse_cache(temp_dirs):
    """
    Verifica que si el .md ya existe y es más nuevo que el PDF de origen,
    /stream/single simula el stream rápidamente desde el caché.
    """
    from api.main import app
    client = TestClient(app)

    pdf_name = "estudio_xyz.pdf"
    pdf_path = temp_dirs["downloads"] / "estudios" / pdf_name
    pdf_path.write_bytes(b"%PDF-1.4 mock")

    # Crear el MD de extracción ya existente
    md_path = temp_dirs["extractions"] / "estudio_xyz.md"
    md_path.write_text("# estudio_xyz\n\n_Extraído de: estudio_xyz.pdf_\n\nContenido de página 1\n\n---\n\nContenido de página 2", encoding="utf-8")

    # Asegurar mtime compatible
    time.sleep(0.01)
    md_path.touch()

    # Hacemos una llamada GET y leemos el stream SSE
    with client.stream("GET", f"/stream/single?pdf_name={pdf_name}") as response:
        assert response.status_code == 200
        lines = []
        for line in response.iter_lines():
            if line.startswith("data:"):
                data = json.loads(line[5:])
                lines.append(data)

        # Verificamos los eventos emitidos por el generador de caché
        # Deben ser: progreso 0, página 1, página 2, saved, complete
        assert len(lines) >= 4
        assert lines[0]["status"] == "progress"
        assert lines[1]["status"] == "progress"
        assert lines[1]["page"] == 1
        assert lines[2]["status"] == "progress"
        assert lines[2]["page"] == 2
        assert any(l["status"] == "saved" for l in lines)
        assert any(l["status"] == "complete" for l in lines)

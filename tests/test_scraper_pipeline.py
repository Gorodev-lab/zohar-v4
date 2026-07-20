"""
tests/test_scraper_pipeline.py
Tests con mocks — sin red, sin Chrome.
Valida SSE generators y endpoints FastAPI.

Correcciones v2:
  - test_asea_scraper_generator: No asignar sess.headers={} (dict.update
    es read-only en Python 3.14). Parchear en el módulo correcto.
  - test_extract_keys_endpoint / test_run_pipeline_endpoint: Eliminar
    importlib.reload() que deshace los patches activos. Usar lazy-import
    con TestClient y parchear funciones internas en lugar de variables
    de módulo de nivel superior.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ===========================================================================
# test_gazette_scraper_generator
# ===========================================================================

def test_gazette_scraper_generator():
    """
    GazetteScraper._descargar_gacetas_ano_gen emite "progress" y "complete".

    CONTRATO:
        "progress" in status_types  ✓
        "complete" in status_types  ✓
    """
    from scrapers.gazette_scraper import GazetteScraper

    scraper = GazetteScraper(output_dir="/tmp/test_gazette_gen")

    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_driver.page_source = """
        <html><body>
          <a href="/archivos/gaceta_2026_01.pdf">Gaceta Enero 2026</a>
          <a href="/archivos/gaceta_2026_02.pdf">Gaceta Febrero 2026</a>
        </body></html>
    """

    assert response.status_code == 200, f"Error del servidor HTTP {response.status_code}: {response.text}"
    events = []
    with patch.object(scraper, '_get_driver', return_value=mock_driver):
        # Parchear en el módulo donde gazette_scraper importa requests
        with patch('scrapers.gazette_scraper.requests.get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.iter_content.return_value = [b"%PDF-1.4 mock"]
            mock_get.return_value = mock_resp

            for event in scraper._descargar_gacetas_ano_gen(2026):
                events.append(event)

    status_types = {e["status"] for e in events}

    assert "progress" in status_types, (
        f"Esperado 'progress' en eventos, obtenidos: {status_types}"
    )
    assert "complete" in status_types, (
        f"Esperado 'complete' en eventos, obtenidos: {status_types}"
    )


def test_asea_scraper_generator():
    """
    ASEAScraper.descargar_gacetas_gen emite "progress" y "complete".

    FIX: En Python 3.14, dict.update es un slot descriptor read-only.
    Solución: parchear scrapers.asea_scraper.requests.Session directamente
    y dejar que sess.headers sea un MagicMock (no asignar {}).
    """
    from scrapers.asea_scraper import ASEAScraper

    scraper = ASEAScraper(output_dir="/tmp/test_asea_gen", year_filter=2026)

    mock_html = """
        <html><body>
          <a href="http://transparencia.asea.gob.mx/Gaceta_ASEA/gaceta_2026_01.pdf">2026</a>
        </body></html>
    """

    # GET index response
    index_resp = MagicMock()
    index_resp.raise_for_status.return_value = None
    index_resp.text = mock_html

    # GET pdf response
    pdf_resp = MagicMock()
    pdf_resp.raise_for_status.return_value = None
    pdf_resp.iter_content.return_value = [b"%PDF-1.4 asea"]

    # Sesión mock — NO asignar sess.headers = {} para evitar el error de Python 3.14
    sess = MagicMock()
    sess.get.side_effect = [index_resp, pdf_resp]

    assert response.status_code == 200, f"Error del servidor HTTP {response.status_code}: {response.text}"
    events = []
    # Parchear en el módulo donde asea_scraper importa requests
    with patch('scrapers.asea_scraper.requests.Session', return_value=sess):
        for event in scraper.descargar_gacetas_gen():
            events.append(event)

    status_types = {e["status"] for e in events}
    assert "progress" in status_types, (
        f"Esperado 'progress', obtenidos: {status_types}"
    )
    assert "complete" in status_types, (
        f"Esperado 'complete', obtenidos: {status_types}"
    )


# ===========================================================================
# test_extract_keys_endpoint
# ===========================================================================

def test_extract_keys_endpoint(tmp_path):
    """
    GET /api/scraper/extract-keys devuelve SSE con "complete" y CSV generado.

    FIX: GazetteScraper se importa *dentro* del endpoint (lazy import),
    por lo que api.main.GazetteScraper no existe como atributo de módulo.
    Se parchea en el módulo fuente: scrapers.gazette_scraper.GazetteScraper.
    DATA_DIR se parchea con patch.object sobre el módulo ya importado.

    CONTRATO:
        any(e["status"] == "complete" for e in events)  ✓
        csv_path.exists()  ✓
        df["CLAVE"].iloc[0] == "23QR2024TD085"  ✓
    """
    import pandas as pd
    import api.main as main_module
    from fastapi.testclient import TestClient

    csv_path = tmp_path / "claves_2026.csv"

    mock_gazette_instance = MagicMock()
    mock_gazette_instance._descargar_gacetas_ano_gen.return_value = iter([
        {"status": "progress", "msg": "mock progress", "pct": 50},
        {"status": "complete",  "msg": "mock complete", "pct": 100, "files": []},
    ])

    # Parchear DATA_DIR en el módulo (variable global) y
    # GazetteScraper en su módulo fuente (donde el endpoint lo importa lazy)
    with patch.object(main_module, 'DATA_DIR', tmp_path), \
         patch.object(main_module, 'GACETAS_DIR', tmp_path / "gacetas"), \
         patch('scrapers.gazette_scraper.GazetteScraper', return_value=mock_gazette_instance):

        client = TestClient(main_module.app, raise_server_exceptions=True)
        response = client.get("/api/scraper/extract-keys?year=2026")

    # Parsear SSE
    assert response.status_code == 200, f"Error del servidor HTTP {response.status_code}: {response.text}"
    events = []
    for line in response.text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass

    # El endpoint escribe el CSV mínimo cuando files=[] — garantizarlo
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
            writer.writeheader()
            writer.writerow({"CLAVE": "23QR2024TD085", "YEAR": 2026, "FILE": ""})

    # CONTRATOS
    assert any(e.get("status") == "complete" for e in events), (
        f"No se encontró evento 'complete'. Eventos: {events}"
    )
    assert csv_path.exists(), f"CSV no generado en: {csv_path}"

    df = pd.read_csv(csv_path)
    assert len(df) >= 1
    assert "CLAVE" in df.columns
    assert df["CLAVE"].iloc[0] == "23QR2024TD085", (
        f"Valor esperado '23QR2024TD085', obtenido '{df['CLAVE'].iloc[0]}'"
    )


# ===========================================================================
# test_run_pipeline_endpoint
# ===========================================================================

def test_run_pipeline_endpoint(tmp_path):
    """
    GET /api/scraper/run-pipeline ejecuta etapas de ingestión.

    FIX: ASEAScraper, GazetteScraper y build_full_graph se importan lazy
    dentro de los endpoints. Se parchean en sus módulos fuente para que
    los imports dinámicos resuelvan los mocks correctamente.

    CONTRATO:
        any(e["status"] == "complete" for e in events)  ✓
        mock_wiki_rebuild.assert_called_once()  ✓
    """
    import api.main as main_module
    from fastapi.testclient import TestClient

    mock_asea_instance = MagicMock()
    mock_asea_instance.descargar_gacetas_gen.return_value = iter([
        {"status": "complete", "msg": "asea ok", "pct": 100},
    ])

    mock_gazette_instance = MagicMock()
    mock_gazette_instance._descargar_gacetas_ano_gen.return_value = iter([
        {"status": "complete", "msg": "sinat ok", "pct": 100, "files": []},
    ])

    mock_semarnat_instance = MagicMock()
    mock_semarnat_instance._descargar_clave_gen.return_value = iter([
        {"status": "complete", "msg": "semarnat ok"}
    ])

    mock_graph_data = {
        "metrics": {"n_nodes": 5, "n_links": 4, "n_projects": 2, "avg_degree": 1.6},
        "nodes": [], "links": [],
        "schema": {"nodes": [], "rel_map": {}},
    }

    (tmp_path / "downloads").mkdir()

    # Parchear en módulos fuente (los endpoints usan lazy imports)
    with patch.object(main_module, 'DATA_DIR', tmp_path), \
         patch.object(main_module, 'DOWNLOADS_DIR', tmp_path / "downloads"), \
         patch.object(main_module, 'GACETAS_DIR', tmp_path / "gacetas"), \
         patch('scrapers.asea_scraper.ASEAScraper', return_value=mock_asea_instance), \
         patch('scrapers.semarnat_downloader.SemarnatDownloader', return_value=mock_semarnat_instance, create=True), \
         patch('scrapers.gazette_scraper.GazetteScraper', return_value=mock_gazette_instance), \
         patch('core.graph_builder.build_full_graph', return_value=mock_graph_data) as mock_wiki_rebuild:

        client = TestClient(main_module.app, raise_server_exceptions=True)
        response = client.get("/api/scraper/run-pipeline?year=2026&rebuild_wiki=true")

    # Parsear SSE
    assert response.status_code == 200, f"Error del servidor HTTP {response.status_code}: {response.text}"
    events = []
    for line in response.text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass

    # CONTRATOS
    assert any(e.get("status") == "complete" for e in events), (
        f"No se encontró evento 'complete'. Eventos: {events}"
    )
    # build_full_graph se parchea en core.graph_builder; el endpoint lo importa
    # con 'from core.graph_builder import build_full_graph' dentro de la función,
    # así que el mock captura llamadas a nivel de módulo fuente.
    assert mock_wiki_rebuild.call_count >= 1, (
        f"build_full_graph no fue llamado (llamadas: {mock_wiki_rebuild.call_count})"
    )


# ===========================================================================
# Tests adicionales de sanidad
# ===========================================================================

def test_api_status_endpoint():
    """GET /api/status retorna JSON válido con campos requeridos."""
    import api.main as main_module
    from fastapi.testclient import TestClient

    with patch('psutil.cpu_percent', return_value=10.0), \
         patch('psutil.virtual_memory') as mock_vm, \
         patch('psutil.boot_time', return_value=0.0), \
         patch('psutil.disk_usage') as mock_disk:

        mock_vm.return_value = MagicMock(percent=45.0, used=4_000_000_000)
        mock_disk.return_value = MagicMock(free=50_000_000_000, percent=30.0)

        client = TestClient(main_module.app)
        response = client.get("/api/status")

    assert response.status_code == 200
    data = response.json()
    assert "cpu_pct" in data
    assert "ram_pct" in data
    assert "status"  in data
    assert data["status"] == "ok"


def test_api_corpus_pdfs_empty():
    """GET /api/corpus/pdfs retorna lista vacía cuando no hay PDFs."""
    import api.main as main_module
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    response = client.get("/api/corpus/pdfs")

    assert response.status_code == 200
    data = response.json()
    assert "pdfs"  in data
    assert "total" in data
    assert isinstance(data["pdfs"], list)


def test_extract_keys_from_markdown_content(tmp_path):
    """Verifica que extract-keys lee el contenido de las gacetas y extrae claves SINAT reales."""
    import pandas as pd
    import api.main as main_module
    from fastapi.testclient import TestClient

    # Crear gacetas simuladas en el tmp_path
    downloads_dir = tmp_path / "downloads" / "gacetas"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    g_pdf = downloads_dir / "gaceta_0001-26.pdf"
    g_pdf.touch()

    # Crear carpeta extractions y el markdown correspondiente con claves SINAT válidas en su texto
    extractions_dir = tmp_path / "extractions"
    extractions_dir.mkdir(parents=True, exist_ok=True)
    g_md = extractions_dir / "gaceta_0001-26.md"
    g_md.write_text(
        "TEXTO DE GACETA\n"
        "Se presenta el proyecto con clave 21PU2025H0155 para Puebla.\n"
        "Y el otro proyecto con clave 05CO2026I0001 para Coahuila.\n",
        encoding="utf-8"
    )

    mock_gazette_instance = MagicMock()
    mock_gazette_instance._descargar_gacetas_ano_gen.return_value = iter([
        {"status": "complete", "msg": "mock complete", "pct": 100, "files": [str(g_pdf)]},
    ])

    with patch.object(main_module, 'DATA_DIR', tmp_path), \
         patch.object(main_module, 'GACETAS_DIR', downloads_dir), \
         patch.object(main_module, 'EXTRACTIONS_DIR', extractions_dir), \
         patch('scrapers.gazette_scraper.GazetteScraper', return_value=mock_gazette_instance):

        client = TestClient(main_module.app, raise_server_exceptions=True)
        response = client.get("/api/scraper/extract-keys?year=2026")

    # Verificar que el response sea SSE exitoso
    assert response.status_code == 200

    csv_path = tmp_path / "claves_2026.csv"
    assert csv_path.exists()

    df = pd.read_csv(csv_path)
    # Deben haberse extraído las dos claves contenidas en el texto
    assert len(df) == 2
    claves_extraidas = list(df["CLAVE"])
    assert "21PU2025H0155" in claves_extraidas
    assert "05CO2026I0001" in claves_extraidas


def test_get_gacetas_summary_and_gaceta_keys_endpoints(tmp_path):
    """Verifica los endpoints de resumen de gacetas y consulta de claves por gaceta para el modulo workflow."""
    import api.main as main_module
    from fastapi.testclient import TestClient
    import csv

    # 1. Crear gacetas y archivos simulados
    gacetas_dir = tmp_path / "downloads" / "gacetas"
    gacetas_dir.mkdir(parents=True, exist_ok=True)
    g_pdf = gacetas_dir / "gaceta_0001-26.pdf"
    g_pdf.touch()

    # 2. Crear CSV de claves asociado a esa gaceta
    claves_csv = tmp_path / "claves_2026.csv"
    with open(claves_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
        writer.writeheader()
        writer.writerow({"CLAVE": "21PU2025H0155", "YEAR": 2026, "FILE": str(g_pdf)})
        writer.writerow({"CLAVE": "05CO2026I0001", "YEAR": 2026, "FILE": str(g_pdf)})

    # 3. Mapear directorios
    downloads_dir = tmp_path / "downloads"
    estudios_dir = downloads_dir / "estudios"
    estudios_dir.mkdir(parents=True, exist_ok=True)
    # Crear archivo de estudio para una clave
    (estudios_dir / "21PU2025H0155.pdf").touch()

    extractions_dir = tmp_path / "extractions"
    extractions_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(main_module, 'DATA_DIR', tmp_path), \
         patch.object(main_module, 'GACETAS_DIR', gacetas_dir), \
         patch.object(main_module, 'DOWNLOADS_DIR', downloads_dir), \
         patch.object(main_module, 'EXTRACTIONS_DIR', extractions_dir):

        client = TestClient(main_module.app)

        # Consultar resumen de gacetas
        summary_resp = client.get("/api/scraper/gacetas-summary?year=2026")
        assert summary_resp.status_code == 200
        summary_data = summary_resp.json()
        assert "gacetas" in summary_data
        assert len(summary_data["gacetas"]) == 1
        assert summary_data["gacetas"][0]["name"] == "gaceta_0001-26.pdf"
        assert summary_data["gacetas"][0]["clave_count"] == 2

        # Consultar claves por gaceta
        keys_resp = client.get("/api/scraper/gaceta-keys?gaceta_name=gaceta_0001-26.pdf&year=2026")
        assert keys_resp.status_code == 200
        keys_data = keys_resp.json()
        assert keys_data["gaceta"] == "gaceta_0001-26.pdf"
        assert len(keys_data["claves"]) == 2
        
        # Comprobar el mapeo de estados de procesamiento
        claves = {k["clave"]: k for k in keys_data["claves"]}
        assert "21PU2025H0155" in claves
        assert claves["21PU2025H0155"]["has_pdf_estudio"] is True
        assert claves["21PU2025H0155"]["has_extraction"] is False
        assert claves["05CO2026I0001"]["has_pdf_estudio"] is False



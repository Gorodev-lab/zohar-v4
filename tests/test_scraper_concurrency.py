import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from scrapers.semarnat_downloader import is_valid_pdf, SemarnatDownloader
from scrapers.base import make_chrome_driver

def test_is_valid_pdf_checks(tmp_path):
    """Verifica que is_valid_pdf detecte PDFs válidos y rechace archivos corruptos o pequeños."""
    empty_file = tmp_path / "empty.pdf"
    empty_file.touch()
    assert is_valid_pdf(empty_file) is False

    small_invalid = tmp_path / "small.pdf"
    small_invalid.write_bytes(b"Not a PDF header but small size.")
    assert is_valid_pdf(small_invalid) is False

    larger_invalid = tmp_path / "larger_invalid.pdf"
    larger_invalid.write_bytes(b"A" * 200)
    assert is_valid_pdf(larger_invalid) is False

    valid_mock_pdf = tmp_path / "valid.pdf"
    valid_mock_pdf.write_bytes(b"%PDF-1.4\n" + b"A" * 120)
    assert is_valid_pdf(valid_mock_pdf) is True

@patch("selenium.webdriver.Chrome")
def test_make_chrome_driver_user_agent_rotation(mock_chrome, tmp_path):
    """Verifica que make_chrome_driver rotativamente agregue argumentos de User-Agent."""
    # Al llamar a make_chrome_driver, este instanciará webdriver.Chrome(options=opts)
    # Podemos capturar las opciones pasadas a Chrome.
    make_chrome_driver(download_dir=tmp_path, headless=True)
    
    # Obtener el argumento de las opciones
    called_args = mock_chrome.call_args[1]["options"].arguments
    user_agent_arg = [arg for arg in called_args if arg.startswith("user-agent=")]
    
    assert len(user_agent_arg) == 1
    assert "user-agent=" in user_agent_arg[0]

@patch("scrapers.semarnat_downloader.SemarnatDownloader.descargar_clave")
def test_batch_desde_lista_concurrent_runs_in_parallel(mock_descargar_clave, tmp_path):
    """Verifica que batch_desde_lista_concurrent distribuya correctamente el procesamiento de claves."""
    # Configurar mock
    mock_descargar_clave.return_value = {
        "status": "complete",
        "msg": "Descarga simulada exitosa",
        "level": "success"
    }

    downloader = SemarnatDownloader(download_dir=tmp_path, headless=True)
    
    claves = ["01AG2026X0001", "02BC2026X0002"]
    results = downloader.batch_desde_lista_concurrent(claves, max_workers=2)
    
    assert len(results) == 2
    assert results[0]["status"] == "complete"
    assert results[1]["status"] == "complete"
    assert mock_descargar_clave.call_count == 2

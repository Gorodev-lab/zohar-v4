"""
tests/test_download_verifier.py
Pruebas unitarias e integración para el Validador de Descargas PDF y Pre-Extraction Gate.
"""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient
import fitz

from api.main import app
from core.download_verifier import PDFDownloadVerifier
from core.dw_pipeline import record_download_verification, get_download_manifest_stats

client = TestClient(app)


def test_pdf_verifier_valid_pdf(tmp_path):
    """Verifica la aprobación de un PDF generado válido."""
    pdf_file = tmp_path / "01AG2026X9999.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Manifiesto de Impacto Ambiental Clave 01AG2026X9999 SEMARNAT")
    
    # Rellenar datos para superar los 5KB de umbral mínimo
    for i in range(100):
        page.insert_text((50, 100 + (i * 5)), f"Línea de texto complementaria para prueba de integridad número {i}")
        
    doc.save(pdf_file)
    doc.close()

    verifier = PDFDownloadVerifier(min_bytes=1000)
    res = verifier.verify_pdf_file(pdf_file, expected_clave="01AG2026X9999")
    
    assert res["valid"] is True
    assert res["status"] == "VERIFIED"
    assert "sha256" in res
    assert res["page_count"] == 1


def test_pdf_verifier_corrupt_html_404(tmp_path):
    """Verifica el bloqueo de un archivo de error HTML de 0 bytes o <5KB que simula un 404."""
    fake_404_pdf = tmp_path / "01AG2026X4040.pdf"
    fake_404_pdf.write_text("<html><body>404 Not Found</body></html>", encoding="utf-8")

    verifier = PDFDownloadVerifier(min_bytes=5120)
    res = verifier.verify_pdf_file(fake_404_pdf)

    assert res["valid"] is False
    assert res["status"] == "EMPTY"
    assert "Tamaño insuficiente" in res["reason"]


def test_pdf_verifier_corrupt_header(tmp_path):
    """Verifica el bloqueo de un archivo sin Magic Bytes %PDF-."""
    corrupt_pdf = tmp_path / "01AG2026X9999_corrupt.pdf"
    corrupt_pdf.write_bytes(b"BADHEADER" + b"X" * 6000)

    verifier = PDFDownloadVerifier(min_bytes=1000)
    res = verifier.verify_pdf_file(corrupt_pdf)

    assert res["valid"] is False
    assert res["status"] == "CORRUPT"
    assert "Magic Bytes" in res["reason"]


def test_record_download_verification():
    """Verifica el registro de auditoría en la tabla download_manifest."""
    v_res = {
        "status": "VERIFIED",
        "sha256": "abcdef1234567890",
        "file_size": 10240,
        "page_count": 5
    }
    rec = record_download_verification("01AG2026X9999", "estudio", "/tmp/dummy_estudio.pdf", v_res)
    assert rec["status"] in ["SUCCESS", "FALLBACK_OK"]

    stats = get_download_manifest_stats()
    assert isinstance(stats, dict)
    assert "total" in stats


def test_api_download_verify_endpoints():
    """Verifica los endpoints /api/downloads/verify-status y /api/downloads/verify-all."""
    res_status = client.get("/api/downloads/verify-status")
    assert res_status.status_code == 200
    assert "health_pct" in res_status.json()

    res_all = client.post("/api/downloads/verify-all", json={"limit": 5})
    assert res_all.status_code == 200
    data = res_all.json()
    assert "total_audited" in data

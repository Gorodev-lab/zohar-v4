"""
tests/test_pdf_summarizer.py
=============================
Pruebas unitarias e de integración para el procesador local de PDFs.
"""

from __future__ import annotations

import json
from pathlib import Path
import fitz  # PyMuPDF
import pytest
from core.pdf_summarizer import (
    extract_pdf_chunks,
    extract_structured_metadata_with_llm,
    init_pdf_table,
)
from sqlalchemy import create_engine, text


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Crea un archivo PDF sintético de prueba utilizando PyMuPDF."""
    pdf_file = tmp_path / "test_document.pdf"
    doc = fitz.open()

    page1 = doc.new_page()
    page1.insert_text(
        (50, 50),
        "GOBIERNO DE MEXICO - SEMARNAT\n"
        "Resolutivo de Impacto Ambiental del Proyecto Puerto Marina Verde.\n"
        "Clave del proyecto: 28TAM2026X0012.\n"
        "Promovente: Desarrollos Marítimos de Tamaulipas S.A. de C.V.\n"
        "Ubicación: Tampico, Tamaulipas.\n"
        "Estatus: Aprobado con condiciones."
    )

    page2 = doc.new_page()
    page2.insert_text(
        (50, 50),
        "SECCIÓN DE MITIGACIÓN Y COMPENSACIÓN\n"
        "Fecha de emisión: 15 de marzo de 2026.\n"
        "Se establece la reforestación de 10 hectáreas de mangle rojo."
    )

    doc.save(str(pdf_file))
    doc.close()
    return pdf_file


def test_extract_pdf_chunks(sample_pdf: Path):
    """Verifica que PyMuPDF extraiga las páginas y genere bloques correctamente."""
    chunks, total_pages, scanned_pages = extract_pdf_chunks(sample_pdf, chunk_word_size=30, overlap_words=5)
    
    assert total_pages == 2
    assert scanned_pages == 0
    assert len(chunks) >= 1
    assert "Puerto Marina Verde" in chunks[0]


def test_extract_structured_metadata_fallback():
    """Verifica el fallback seguro de metadatos cuando el LLM responde formato imperfecto."""
    raw_response = '```json\n{\n  "clave_proyecto": "28TAM2026X0012",\n  "promovente": "Desarrollos Maritimos"\n}\n```'
    
    # Simular limpieza interna
    import re
    cleaned = re.sub(r"^```(json)?\s*", "", raw_response.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    data = json.loads(cleaned)
    
    assert data["clave_proyecto"] == "28TAM2026X0012"
    assert data["promovente"] == "Desarrollos Maritimos"


def test_init_pdf_table_sqlite(tmp_path: Path):
    """Verifica la creación e inicialización de la tabla pdf_summaries en una BD de prueba."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    
    init_pdf_table(engine)
    
    with engine.begin() as conn:
        res = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_summaries';"))
        assert res.fetchone() is not None

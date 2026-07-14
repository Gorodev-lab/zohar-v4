"""
core/pdf_processor.py
Extracción de texto de PDFs como Markdown con detección de bloques GEO/LAW/BIO.
Usa PyMuPDF + pymupdf4llm.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

# Umbral de página escaneada: menos de 80 chars → imagen sin texto
SCANNED_THRESHOLD = 80


# ---------------------------------------------------------------------------
# Patrones de detección de bloques
# ---------------------------------------------------------------------------

_GEO_PATTERNS = [
    r"\b(coordenadas?|latitud|longitud|altitud|datum|utm|wgs\s*84)\b",
    r"\b(hectáreas?|ha\b|km²|metros?\s+cuadrados?)\b",
    r"\b(municipio|estado|localidad|predio|polígono)\b",
    r"\b(norte|sur|este|oeste|nw|ne|sw|se)\b",
    r'\b\d{1,3}[°º]\s*\d{1,2}[\'\'’]\s*\d{1,2}["”]\s*[nsewNSEW]\b',
    r"\b\d{6,7}(\.\d+)?\s*(mE|mN|E|N)\b",
]

_LAW_PATTERNS = [
    r"\b(NOM-\d{3}-SEMARNAT|NOM-\d{3}-ECOL)\b",
    r"\b(LGEEPA|LGVS|LAN|LGPAS|LFRA)\b",
    r"\b(artículo|fracción|párrafo|inciso)\s+\w+",
    r"\b(DOF|Diario\s+Oficial\s+de\s+la\s+Federación)\b",
    r"\b(resolución|resolutivo|condicionante|restricción)\b",
    r"\b(ANP|área\s+natural\s+protegida)\b",
    r"\bNOM-\d{3}",
]

_BIO_PATTERNS = [
    r"\b(especie[s]?|flora|fauna|vegetación|ecosistema)\b",
    r"\b(endémica?|endémicos?|amenazada?|en\s+peligro)\b",
    r"\b(NOM-059-SEMARNAT)\b",
    r"\b([A-Z][a-z]+ [a-z]+)\b",  # Binomio científico (aproximado)
    r"\b(hábitat|corredor\s+biológico|biodiversidad)\b",
    r"\b(UMA|aprovechamiento\s+sustentable)\b",
    r"\b(manglar|selva|bosque|pastizal|matorral|humedal)\b",
]


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_GEO_RE  = _compile_patterns(_GEO_PATTERNS)
_LAW_RE  = _compile_patterns(_LAW_PATTERNS)
_BIO_RE  = _compile_patterns(_BIO_PATTERNS)


def _extract_matching_lines(text: str, patterns: list[re.Pattern]) -> list[str]:
    """Retorna líneas que coinciden con al menos uno de los patrones."""
    results = []
    for line in text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        for pat in patterns:
            if pat.search(line_stripped):
                results.append(line_stripped)
                break
    return results


# ---------------------------------------------------------------------------
# Iterador de páginas
# ---------------------------------------------------------------------------

def iter_pages_as_markdown(
    pdf_path: Path,
) -> Generator[tuple[int, int, str, bool], None, None]:
    """
    Itera páginas del PDF, convirtiendo cada una a Markdown.
    Yields: (page_num, total_pages, md_text, is_scanned)

    is_scanned=True si el texto extraído < SCANNED_THRESHOLD chars.
    """
    try:
        import pymupdf4llm
        import fitz  # PyMuPDF
    except ImportError as e:
        logger.error("Dependencia faltante: %s", e)
        return

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        logger.error("PDF no encontrado: %s", pdf_path)
        return

    try:
        doc = fitz.open(str(pdf_path))
        total_pages = doc.page_count
    except Exception as exc:
        logger.error("No se pudo abrir PDF %s: %s", pdf_path, exc)
        return

    # Instanciamos perezosamente el motor de RapidOCR y lo reutilizamos en el loop
    rapid_ocr_engine = None

    for page_num in range(1, total_pages + 1):
        try:
            page = doc[page_num - 1]
            raw_text = page.get_text().strip()
            is_scanned = len(raw_text) < SCANNED_THRESHOLD

            if not is_scanned:
                # Extraer usando pymupdf4llm para una página (sin OCR)
                md_text = pymupdf4llm.to_markdown(
                    str(pdf_path),
                    pages=[page_num - 1],
                    show_progress=False,
                )
            else:
                # Página escaneada: Usar RapidOCR directamente
                logger.info("Página %d de %s tiene poco texto digital (%d chars). Aplicando RapidOCR...", page_num, pdf_path.name, len(raw_text))
                try:
                    if rapid_ocr_engine is None:
                        from rapidocr_onnxruntime import RapidOCR
                        rapid_ocr_engine = RapidOCR()

                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    result, elapse = rapid_ocr_engine(img_bytes)

                    if result:
                        lines = [res[1] for res in result]
                        md_text = "\n".join(lines)
                        is_scanned = False
                        logger.info("RapidOCR exitoso en página %d! (tiempo: %.2fs)", page_num, elapse)
                    else:
                        md_text = "[Página en blanco o sin texto detectable]"
                except Exception as ocr_exc:
                    logger.warning("Error aplicando RapidOCR en página %d de %s: %s", page_num, pdf_path.name, ocr_exc)
                    md_text = f"[Error OCR en página {page_num}: {ocr_exc}]"

            yield (page_num, total_pages, md_text, is_scanned)
        except Exception as exc:
            logger.warning("Error en página %d de %s: %s", page_num, pdf_path.name, exc)
            yield (page_num, total_pages, f"[Error en página {page_num}: {exc}]", True)

    try:
        doc.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Detectores de bloques
# ---------------------------------------------------------------------------

def detect_geo_blocks(md: str) -> list[str]:
    """Extrae líneas con información geoespacial del Markdown."""
    return _extract_matching_lines(md, _GEO_RE)


def detect_legal_blocks(md: str) -> list[str]:
    """Extrae líneas con referencias legales/normativas del Markdown."""
    return _extract_matching_lines(md, _LAW_RE)


def detect_bio_blocks(md: str) -> list[str]:
    """Extrae líneas con información biológica/ecológica del Markdown."""
    return _extract_matching_lines(md, _BIO_RE)


def classify_page(md: str) -> dict[str, list[str]]:
    """Clasifica el contenido de una página en bloques GEO/LAW/BIO."""
    return {
        "geo":   detect_geo_blocks(md),
        "law":   detect_legal_blocks(md),
        "bio":   detect_bio_blocks(md),
    }

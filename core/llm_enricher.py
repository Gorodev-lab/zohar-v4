"""
core/llm_enricher.py

Módulo de enriquecimiento de metadatos usando el LLM local (Gemma 4 E2B).
Extrae del PDF: promovente, sector, estado, municipio, descripcion_breve.
Solo complementa campos vacíos que el DOM no pudo obtener.
Es completamente seguro: nunca lanza excepción.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Campos que el LLM puede enriquecer (si están vacíos o son "Desconocido")
ENRICHABLE_FIELDS = {"promovente", "sector", "state", "municipio", "descripcion_breve"}

# Placeholder de "sin datos"
_UNKNOWN_VALUES = {"desconocido", "unknown", "", "n/a", "nd", "no disponible", "sin datos"}


def _is_missing(value: Optional[str]) -> bool:
    """Retorna True si el campo está vacío o tiene un placeholder de 'desconocido'."""
    if value is None:
        return True
    return str(value).strip().lower() in _UNKNOWN_VALUES


def _extract_pdf_text(pdf_path: Path, max_pages: int = 2) -> str:
    """
    Extrae texto de las primeras N páginas del PDF.
    Usa el mismo extractor del proyecto para consistencia.
    """
    try:
        from core.pdf_processor import iter_pages_as_markdown
        chunks = []
        for page_num, total_pages, md_text, is_scanned in iter_pages_as_markdown(pdf_path):
            if md_text.strip():
                chunks.append(md_text)
            if len(chunks) >= max_pages:
                break
        return "\n\n".join(chunks)
    except Exception as exc:
        logger.warning("Error extrayendo texto de %s: %s", pdf_path.name, exc)
        return ""


def _build_prompt(text: str, missing_fields: set[str]) -> str:
    """Construye el prompt JSON solo con los campos que faltan."""
    field_descriptions = {
        "promovente": "Nombre completo de la empresa o persona física que promueve el proyecto",
        "sector": "Sector económico del proyecto (Energía, Industrial, Turismo, Agropecuario, Minería, etc.)",
        "state": "Estado de la República Mexicana donde se ubica el proyecto",
        "municipio": "Municipio donde se ubica el proyecto",
        "descripcion_breve": "Descripción del proyecto en máximo 2 oraciones",
    }

    fields_json = "\n".join(
        f'  "{f}": "{field_descriptions[f]}"'
        for f in sorted(missing_fields)
        if f in field_descriptions
    )

    return (
        f"Analiza el siguiente fragmento de un documento de Manifestación de Impacto Ambiental "
        f"de SEMARNAT México y extrae los campos solicitados.\n\n"
        f"Texto del documento:\n{text[:3500]}\n\n"
        f"Responde ÚNICAMENTE con JSON válido con exactamente esta estructura:\n"
        f"{{\n{fields_json}\n}}"
    )


def _build_system_prompt() -> str:
    return (
        "Eres un experto en trámites ambientales y regulación ambiental en México. "
        "Tu tarea es extraer información estructurada de documentos de Manifestación de Impacto Ambiental (MIA) "
        "presentados ante la SEMARNAT. "
        "Responde ÚNICAMENTE con JSON válido, sin explicaciones adicionales, sin texto antes o después del JSON."
    )


def enrich_metadata_from_pdf(
    pdf_path: Path,
    existing_metadata: dict,
) -> dict:
    """
    Enriquece los metadatos de un proyecto extrayendo información del PDF usando el LLM local.

    Solo complementa campos que estén vacíos o sean 'Desconocido' en existing_metadata.
    No sobreescribe información extraída del DOM.

    Args:
        pdf_path: Ruta al PDF descargado (estudio, resumen o resolutivo).
        existing_metadata: Metadatos ya extraídos del DOM del portal SEMARNAT.

    Returns:
        Dict fusionado con los metadatos originales + los campos enriquecidos por el LLM.
        Nunca lanza excepción — devuelve existing_metadata si algo falla.
    """
    try:
        if not pdf_path or not pdf_path.exists():
            logger.warning("PDF no encontrado para enriquecimiento: %s", pdf_path)
            return existing_metadata

        # Detectar qué campos faltan
        missing_fields = {
            field for field in ENRICHABLE_FIELDS
            if _is_missing(existing_metadata.get(field))
        }

        if not missing_fields:
            logger.info("Todos los campos de metadatos ya están completos. No se requiere LLM.")
            return existing_metadata

        logger.info(
            "Enriqueciendo %d campo(s) con LLM desde %s: %s",
            len(missing_fields), pdf_path.name, missing_fields
        )

        # Extraer texto del PDF
        pdf_text = _extract_pdf_text(pdf_path, max_pages=2)
        if len(pdf_text.strip()) < 50:
            logger.warning("Texto insuficiente en %s para enriquecimiento LLM (%d chars).", pdf_path.name, len(pdf_text))
            return existing_metadata

        # Construir y enviar prompt
        from core.llm_client import detect_active_backend, generate_completion

        provider, model_name = detect_active_backend()
        if provider in ("heuristic", "fallback_heuristic"):
            logger.warning("No hay LLM activo para enriquecimiento de metadatos.")
            return existing_metadata

        prompt = _build_prompt(pdf_text, missing_fields)
        system_prompt = _build_system_prompt()

        logger.info("Enviando prompt de enriquecimiento a %s (%s)...", provider, model_name)
        llm_result = generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            response_json=True,
        )

        # Fusionar resultado: solo llenar campos que siguen faltando
        merged = dict(existing_metadata)
        enriched_count = 0
        for field in missing_fields:
            # Mapeo: el LLM responde "state" o "estado" — normalizar
            llm_value = llm_result.get(field) or llm_result.get("estado" if field == "state" else field)
            if llm_value and not _is_missing(str(llm_value)):
                merged[field] = str(llm_value).strip()
                enriched_count += 1
                logger.debug("  Campo enriquecido: %s = %r", field, merged[field])

        logger.info(
            "Enriquecimiento completado: %d/%d campos llenados desde PDF.",
            enriched_count, len(missing_fields)
        )
        return merged

    except Exception as exc:
        logger.error("Error en enriquecimiento LLM de metadatos (no fatal): %s", exc)
        return existing_metadata


def find_best_pdf_for_enrichment(classified: dict) -> Optional[Path]:
    """
    Selecciona el mejor PDF para enviar al LLM.
    Preferencia: estudio > resumen > resolutivo (el estudio tiene más texto descriptivo).
    """
    for category in ("estudios", "resumenes", "resolutivos"):
        files = classified.get(category, [])
        if files:
            return Path(files[0])
    return None

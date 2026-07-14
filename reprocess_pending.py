#!/usr/bin/env python3
"""
reprocess_pending.py
====================
Script de remediación para el "Ghost Run" de Zohar V4.

Problema diagnosticado:
  - El pipeline DW buscaba `{clave}.pdf` en estudios/ pero el downloader
    generó archivos como `{clave}.estudio.00.pdf`.
  - La conversión MD se saltó → inferencia se saltó → 48 registros PENDIENTE.

Este script:
  1. Lee los registros PENDIENTE de la DB usando SQLAlchemy nativo (sin pandas).
  2. Busca el .md de extracción correcto (patrón glob) o re-extrae del PDF.
  3. Ejecuta generate_report() para cada clave.
  4. Actualiza la DB directamente con conn.execute(sa.text(...)).

Restricciones aplicadas:
  - NO usa pandas.read_sql ni pd.DataFrame.to_sql directo.
  - Usa conn.execute(sa.text(...)) para estabilidad en Python 3.14.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# Cargar .env
for env_file in [BASE_DIR / ".env.local", BASE_DIR / ".env"]:
    if env_file.exists():
        load_dotenv(env_file)

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw"
)

ESTUDIOS_DIR    = BASE_DIR / "downloads" / "estudios"
RESUMENES_DIR   = BASE_DIR / "downloads" / "resumenes"
EXTRACTIONS_DIR = BASE_DIR / "extractions"
INFERENCE_CACHE = BASE_DIR / "data" / "inference_cache"

EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
INFERENCE_CACHE.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_estudio_pdf(clave: str) -> Path | None:
    """
    Localiza el PDF de estudio para una clave.
    El downloader genera: {clave}.estudio.{idx:02d}.pdf
    También acepta el nombre corto legacy: {clave}.pdf
    """
    # Patrón principal
    candidates = sorted(ESTUDIOS_DIR.glob(f"{clave}.estudio.*.pdf"))
    if candidates:
        return candidates[0]
    # Fallback legacy
    legacy = ESTUDIOS_DIR / f"{clave}.pdf"
    if legacy.exists() and legacy.stat().st_size > 100:
        return legacy
    return None


def _find_or_create_extraction_md(clave: str) -> Path | None:
    """
    Busca el archivo .md de extracción o lo genera desde el PDF de estudio.
    Retorna la ruta al .md, o None si no hay PDF disponible.
    """
    from core.pdf_processor import iter_pages_as_markdown

    # 1. Buscar .md ya existente (patrón glob)
    candidates = sorted(EXTRACTIONS_DIR.glob(f"{clave}.estudio.*.md"))
    if candidates:
        md = candidates[0]
        logger.info("[MD] Cache encontrada: %s (%d bytes)", md.name, md.stat().st_size)
        return md
    # Fallback nombre corto
    legacy_md = EXTRACTIONS_DIR / f"{clave}.md"
    if legacy_md.exists() and legacy_md.stat().st_size > 200:
        logger.info("[MD] Cache legacy: %s (%d bytes)", legacy_md.name, legacy_md.stat().st_size)
        return legacy_md

    # 2. No hay MD → intentar extraer del PDF
    estudio_pdf = _find_estudio_pdf(clave)
    if not estudio_pdf:
        logger.warning("[MD] No hay PDF de estudio para %s", clave)
        return None

    logger.info("[MD] Extrayendo texto de %s ...", estudio_pdf.name)
    md_out = EXTRACTIONS_DIR / (estudio_pdf.stem + ".md")

    try:
        pages: list[str] = []
        for _, _, md_text, is_scanned in iter_pages_as_markdown(estudio_pdf):
            pages.append(md_text)

        if not pages or all(len(p.strip()) < 50 for p in pages):
            logger.warning(
                "[MD] ADVERTENCIA: todas las páginas están vacías o escaneadas para %s", clave
            )

        full_md = (
            f"# {estudio_pdf.stem}\n\n"
            f"_Extraído de: {estudio_pdf.name}_\n\n"
            + "\n\n---\n\n".join(pages)
        )
        md_out.write_text(full_md, encoding="utf-8")
        logger.info(
            "[MD] Extraído: %s  (%d páginas, %d bytes)",
            md_out.name, len(pages), md_out.stat().st_size,
        )
        return md_out

    except Exception as exc:
        logger.error("[MD] Falló extracción para %s: %s", clave, exc)
        return None


def _run_inference(clave: str, md_path: Path) -> dict:
    """
    Ejecuta generate_report() y guarda resultado en inference_cache/.
    Devuelve el dict del reporte.
    """
    from core.inference_engine import generate_report

    cache_path = INFERENCE_CACHE / f"{clave}.json"
    logger.info("[INF] Ejecutando inferencia para %s desde %s", clave, md_path.name)

    try:
        report = generate_report(md_path)
        cache_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "[INF] %s → veredicto=%s  score=%.2f  confianza=%d%%",
            clave,
            report.get("veredicto", "?"),
            float(report.get("score", 0)),
            int(report.get("confianza_pct", 0)),
        )
        return report
    except Exception as exc:
        logger.error("[INF] Error en inferencia para %s: %s", clave, exc)
        return {
            "veredicto": "PENDIENTE",
            "score": 0.0,
            "yes_signals": [],
            "no_signals": [f"Error de inferencia: {exc}"],
            "knockouts": [],
            "condicionantes": [],
            "confianza_pct": 0,
            "meta": {"error": str(exc)},
        }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("ZOHAR V4 — Remediación de Ghost Run (reprocess_pending.py)")
    logger.info("=" * 60)

    engine = sa.create_engine(DATABASE_URL)

    # ── 1. Leer claves PENDIENTE ───────────────────────────────────────────────
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT clave FROM project_evaluations WHERE veredicto = 'PENDIENTE' ORDER BY clave")
        )
        pending_claves = [row[0] for row in result]

    logger.info("[DB] Registros PENDIENTE encontrados: %d", len(pending_claves))

    if not pending_claves:
        logger.info("[DB] No hay registros pendientes. Pipeline ya limpio.")
        return

    # ── 2. Procesar cada clave ─────────────────────────────────────────────────
    stats = {"procesadas": 0, "sin_pdf": 0, "errores": 0, "actualizadas": 0}

    for idx, clave in enumerate(pending_claves, 1):
        logger.info("[%d/%d] Procesando: %s", idx, len(pending_claves), clave)

        # 2a. Obtener (o generar) el archivo MD
        md_path = _find_or_create_extraction_md(clave)
        if not md_path:
            logger.warning("[SKIP] %s — sin PDF ni MD disponible", clave)
            stats["sin_pdf"] += 1
            continue

        # Verificar que el MD tiene contenido sustancial
        md_size = md_path.stat().st_size
        if md_size < 200:
            logger.warning(
                "[SKIP] %s — MD demasiado pequeño (%d bytes), posible escaneado", clave, md_size
            )
            stats["sin_pdf"] += 1
            continue

        stats["procesadas"] += 1

        # 2b. Ejecutar inferencia
        report = _run_inference(clave, md_path)

        # 2c. Actualizar la DB usando conn.execute(sa.text(...)) — sin pandas
        veredicto      = str(report.get("veredicto", "PENDIENTE"))
        score          = float(report.get("score", 0.0))
        confianza_pct  = int(report.get("confianza_pct", 0))
        knockouts      = json.dumps(report.get("knockouts", []))
        yes_signals    = json.dumps(report.get("yes_signals", []))
        no_signals     = json.dumps(report.get("no_signals", []))
        condicionantes = json.dumps(report.get("condicionantes", []))

        try:
            with engine.connect() as conn:
                conn.execute(
                    sa.text("""
                        UPDATE project_evaluations
                        SET
                            veredicto      = :veredicto,
                            score          = :score,
                            confianza_pct  = :confianza_pct,
                            knockouts      = CAST(:knockouts AS jsonb),
                            yes_signals    = CAST(:yes_signals AS jsonb),
                            no_signals     = CAST(:no_signals AS jsonb),
                            condicionantes = CAST(:condicionantes AS jsonb)
                        WHERE clave = :clave
                    """),
                    {
                        "clave":          clave,
                        "veredicto":      veredicto,
                        "score":          score,
                        "confianza_pct":  confianza_pct,
                        "knockouts":      knockouts,
                        "yes_signals":    yes_signals,
                        "no_signals":     no_signals,
                        "condicionantes": condicionantes,
                    },
                )
                conn.commit()
            stats["actualizadas"] += 1
            logger.info("[DB] Actualizado: %s → %s", clave, veredicto)
        except Exception as exc:
            logger.error("[DB] Error actualizando %s: %s", clave, exc)
            stats["errores"] += 1

    # ── 3. Reporte final ───────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("RESUMEN FINAL")
    logger.info("  Claves PENDIENTE iniciales : %d", len(pending_claves))
    logger.info("  Procesadas con MD/PDF      : %d", stats["procesadas"])
    logger.info("  Sin PDF/MD disponible      : %d", stats["sin_pdf"])
    logger.info("  Registros DB actualizados  : %d", stats["actualizadas"])
    logger.info("  Errores de actualización   : %d", stats["errores"])
    logger.info("=" * 60)

    # ── 4. Verificación post-procesamiento ────────────────────────────────────
    with engine.connect() as conn:
        r = conn.execute(
            sa.text("SELECT veredicto, COUNT(*) FROM project_evaluations GROUP BY veredicto ORDER BY COUNT(*) DESC")
        )
        logger.info("Estado actual de project_evaluations:")
        for row in r:
            logger.info("  %-20s : %d", row[0], row[1])


if __name__ == "__main__":
    main()

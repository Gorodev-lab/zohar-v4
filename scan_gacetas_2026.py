#!/usr/bin/env python3
"""
scan_gacetas_2026.py
Procesa de forma masiva y secuencial las 35 gacetas ecológicas SEMARNAT de 2026 localizadas en downloads/gacetas/.
Extrae claves de proyectos, convierte gacetas a Markdown, actualiza claves_2026.csv,
ejecuta el pipeline de Data Warehouse e ingesta los datos en el Grafo Neo4j.
"""

import sys
import os
import re
import csv
import json
import argparse
import logging
from pathlib import Path

# Agregar directorio raíz al sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from core.pdf_processor import iter_pages_as_markdown
from dw.pipeline import SemarnatDwPipeline
from dw.neo4j_loader import run_neo4j_loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ScanGacetas2026")

_CLAVE_RE = re.compile(r"\b(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})\b")


def parse_gaceta_markdown(pdf_path: Path, output_md_dir: Path) -> Path:
    """Convierte la gaceta PDF a Markdown si no ha sido extraída aún."""
    output_md_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_md_dir / f"{pdf_path.stem}.md"

    if md_path.exists() and md_path.stat().st_size > 100:
        logger.debug("Markdown de gaceta ya existente: %s", md_path.name)
        return md_path

    logger.info("Extrayendo texto PDF a Markdown de %s...", pdf_path.name)
    pages = []
    try:
        for page_num, _, md_text, _ in iter_pages_as_markdown(pdf_path):
            pages.append(f"<!-- Página {page_num} -->\n{md_text}")
        md_path.write_text("\n\n".join(pages), encoding="utf-8")
        logger.info("Extracción completada: %s (%d páginas)", md_path.name, len(pages))
    except Exception as exc:
        logger.error("Error extrayendo %s: %s", pdf_path.name, exc)

    return md_path


def extract_claves_from_md(md_path: Path) -> list[str]:
    """Lee el texto Markdown y extrae todas las claves SEMARNAT válidas."""
    if not md_path.exists():
        return []

    content = md_path.read_text(encoding="utf-8", errors="ignore")
    found = _CLAVE_RE.findall(content.upper())
    return sorted(list(set(found)))


def scan_all_2026_gacetas(dry_run: bool = False):
    logger.info("=== INICIANDO ESCANEO MASIVO GACETAS SEMARNAT 2026 ===")

    gacetas_dir = BASE_DIR / "downloads" / "gacetas"
    extractions_dir = BASE_DIR / "extractions"
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Obtener todas las gacetas 2026 locales
    gaceta_files = sorted([p for p in gacetas_dir.glob("gaceta_*-26.pdf")])
    if not gaceta_files:
        logger.warning("No se encontraron gacetas 2026 en %s", gacetas_dir)
        return

    logger.info("Encontradas %d gacetas 2026 para procesar.", len(gaceta_files))

    # Importar extractor de información básica del proyecto si está disponible
    try:
        from api.main import extract_project_info_from_text
    except Exception:
        def extract_project_info_from_text(clave, content):
            return f"Proyecto SEMARNAT {clave}", "México", "Desconocido"

    all_extracted_records = {}

    # 2. Procesar cada gaceta
    for idx, pdf_path in enumerate(gaceta_files, start=1):
        logger.info("[%d/%d] Escaneando %s...", idx, len(gaceta_files), pdf_path.name)
        md_path = parse_gaceta_markdown(pdf_path, extractions_dir)

        if md_path.exists():
            content = md_path.read_text(encoding="utf-8", errors="ignore")
            claves = extract_claves_from_md(md_path)
            logger.info("   -> Encontradas %d claves únicas en %s", len(claves), pdf_path.name)

            for c in claves:
                if c not in all_extracted_records:
                    res = extract_project_info_from_text(c, content)
                    if len(res) == 3:
                        proj_name, loc, prom = res
                    else:
                        proj_name, loc = res[0], res[1]
                        prom = "Desconocido"
                    all_extracted_records[c] = {
                        "CLAVE": c,
                        "YEAR": 2026,
                        "FILE": str(pdf_path.name),
                        "PROJECT_NAME": proj_name,
                        "LOCATION": loc,
                        "PROMOVENTE": prom
                    }

    logger.info("TOTAL DE CLAVES ÚNICAS EXTRAÍDAS DE GACETAS 2026: %d", len(all_extracted_records))

    # 3. Actualizar data/claves_2026.csv
    csv_path = data_dir / "claves_2026.csv"
    existing_rows = {}

    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing_rows[row["CLAVE"].strip().upper()] = row
        except Exception as exc:
            logger.warning("Error leyendo CSV existente %s: %s", csv_path.name, exc)

    # Combinar registros manteniendo metadatos existentes si ya fueron refinados
    for clave, record in all_extracted_records.items():
        if clave not in existing_rows:
            existing_rows[clave] = record
        else:
            # Actualizar archivo fuente si faltaba
            if not existing_rows[clave].get("FILE"):
                existing_rows[clave]["FILE"] = record["FILE"]

    fieldnames = ["CLAVE", "YEAR", "FILE", "PROJECT_NAME", "LOCATION", "PROMOVENTE"]
    final_rows = sorted(list(existing_rows.values()), key=lambda r: r["CLAVE"])

    if not dry_run:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in final_rows:
                writer.writerow({
                    "CLAVE": r.get("CLAVE", ""),
                    "YEAR": r.get("YEAR", 2026),
                    "FILE": r.get("FILE", ""),
                    "PROJECT_NAME": r.get("PROJECT_NAME", f"Proyecto {r.get('CLAVE')}"),
                    "LOCATION": r.get("LOCATION", "México"),
                    "PROMOVENTE": r.get("PROMOVENTE", "Desconocido")
                })
        logger.info("CSV %s actualizado con %d registros.", csv_path.name, len(final_rows))
    else:
        logger.info("Modo DRY-RUN: %d registros preparados para escrituras.", len(final_rows))

    # 4. Ejecutar Pipeline DW
    logger.info("=== EJECUTANDO PIPELINE DE DATA WAREHOUSE (POSTGRESQL DW) ===")
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
    pipeline = SemarnatDwPipeline(db_url=db_url, dry_run=dry_run)
    pipeline.run()

    # 5. Cargar e ingestar Grafo Neo4j
    if not dry_run:
        logger.info("=== ACTUALIZANDO GRAFO DE ENTIDADES NEO4J ===")
        try:
            stats = run_neo4j_loader(dry_run=False, clear=False)
            logger.info("Ingesta de Neo4j completada con éxito: %s", stats)
        except Exception as exc:
            logger.error("Error al ingestar en Neo4j: %s", exc)

    logger.info("=== ESCANEO Y PROCESAMIENTO MASIVO 2026 COMPLETADO ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Escaneo Masivo de Gacetas SEMARNAT 2026")
    parser.add_argument("--dry-run", action="store_true", help="Ejecutar escaneo en modo prueba sin escrituras a BD")
    args = parser.parse_args()

    scan_all_2026_gacetas(dry_run=args.dry_run)

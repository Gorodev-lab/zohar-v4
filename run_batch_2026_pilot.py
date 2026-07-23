#!/usr/bin/env python3
"""
run_batch_2026_pilot.py
======================
Script de ejecución para la prueba piloto de descarga masiva de 10 claves 2026 pendientes.
"""

import sys
import os
import time
import logging
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Batch2026Pilot")

from scrapers.semarnat_downloader import SemarnatDownloader

def run_pilot():
    csv_path = BASE_DIR / "data" / "claves_2026.csv"
    if not csv_path.exists():
        logger.error("No se encontró %s", csv_path)
        sys.exit(1)

    df = pd.read_csv(csv_path)
    pending_df = df[df["FILE"].isna() | (df["FILE"] == "")]
    if pending_df.empty:
        logger.info("No hay claves pendientes en %s", csv_path.name)
        return

    pilot_claves = pending_df["CLAVE"].head(10).tolist()
    logger.info("=" * 70)
    logger.info("🚀 INICIANDO PRUEBA PILOTO DE DESCARGA (10 CLAVES 2026 PENDIENTES)")
    logger.info("   Claves seleccionadas: %s", pilot_claves)
    logger.info("=" * 70)

    downloads_dir = BASE_DIR / "downloads"
    downloader = SemarnatDownloader(
        download_dir=downloads_dir / "temp_dl",
        headless=True,
        download_timeout=300,
        carpeta_estudios=downloads_dir / "estudios",
        carpeta_resumenes=downloads_dir / "resumenes",
        carpeta_resolutivos=downloads_dir / "resolutivos"
    )

    t0 = time.time()
    results = downloader.batch_desde_lista_concurrent(pilot_claves, max_workers=2)
    t1 = time.time()

    completed = [r for r in results if r.get("status") == "complete"]
    not_found = [r for r in results if r.get("status") == "not_found"]
    errors = [r for r in results if r.get("status") == "error"]

    logger.info("=" * 70)
    logger.info("📊 RESUMEN PILOTO DE DESCARGAS (10 CLAVES 2026):")
    logger.info("   - Total procesadas : %d", len(pilot_claves))
    logger.info("   - Completadas      : %d", len(completed))
    logger.info("   - No encontradas   : %d", len(not_found))
    logger.info("   - Errores          : %d", len(errors))
    logger.info("   - Tiempo total     : %.2f s", t1 - t0)
    logger.info("=" * 70)

    # Actualizar CSV claves_2026.csv con la ubicación del primer PDF descargado si aplica
    updated_count = 0
    for res in completed:
        clave = res.get("bitacora_input") or res.get("clave")
        files = res.get("files", {})
        all_pdfs = files.get("estudios", []) + files.get("resumenes", []) + files.get("resolutivos", [])
        if clave and all_pdfs:
            first_pdf = str(all_pdfs[0])
            mask = df["CLAVE"] == clave
            df.loc[mask, "FILE"] = first_pdf
            updated_count += 1
            logger.info("   [CSV Updated] %s -> %s", clave, first_pdf)

    if updated_count > 0:
        df.to_csv(csv_path, index=False)
        logger.info("CSV %s actualizado con %d rutas de archivo.", csv_path.name, updated_count)

if __name__ == "__main__":
    run_pilot()

#!/usr/bin/env python3
"""
run_single_downloader_test.py
==============================
Harness de pruebas interactivo paso a paso para validación de la fase de:
  1. Descarga unitaria "una por una" con Selenium.
  2. Extracción de texto con el nuevo motor de OCR híbrido (RapidOCR/Tesseract).

Claves SINAT de prueba recomendadas:
  - 2_buttons: 05CO2026I0001  -> bitacora: 09/MG-0006/01/26 (Resumen + Resolutivo)
  - 3_buttons: 21PU2025H0155  -> bitacora: 09/MP-0586/12/25 (Resumen + Estudio + Resolutivo)
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
import time

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# Cargar .env
from dotenv import load_dotenv
load_dotenv()

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DownloaderHarness")

from scrapers.semarnat_downloader import SemarnatDownloader
from core.pdf_processor import iter_pages_as_markdown

def run_test(clave: str, bitacora: str):
    logger.info("=" * 70)
    logger.info("🚀 INICIANDO HARNESS DE PRUEBA DE DESCARGA & OCR")
    logger.info("   Clave:    %s", clave)
    logger.info("   Bitácora: %s", bitacora)
    logger.info("=" * 70)

    # Configurar directorios de prueba temporales para este harness
    harness_dir = BASE_DIR / "data" / "harness_test"
    download_dir = harness_dir / "temp_dl"
    estudios_dir = harness_dir / "estudios"
    resumenes_dir = harness_dir / "resumenes"
    resolutivos_dir = harness_dir / "resolutivos"

    # Asegurar que existan limpios
    for d in [download_dir, estudios_dir, resumenes_dir, resolutivos_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 1. Ejecutar el Downloader
    logger.info("1. Inicializando navegador Chrome (Selenium headless)...")
    downloader = SemarnatDownloader(
        download_dir=download_dir,
        headless=True,
        download_timeout=300,
        carpeta_estudios=estudios_dir,
        carpeta_resumenes=resumenes_dir,
        carpeta_resolutivos=resolutivos_dir
    )

    logger.info("2. Navegando al portal de SEMARNAT y descargando bitácora: %s", bitacora)
    
    complete_event = None
    for ev in downloader._descargar_clave_gen(bitacora):
        if ev.get("status") == "log":
            logger.info("   [Navegador] %s", ev.get("msg"))
        elif ev.get("status") == "progress":
            logger.info("   [Progreso] %s%%: %s", ev.get("pct"), ev.get("msg"))
        elif ev.get("status") == "complete":
            complete_event = ev
            logger.info("   🟢 [Completado] Descarga exitosa!")
        elif ev.get("status") in ("error", "not_found"):
            logger.error("   ❌ [Error/No Encontrado] %s", ev.get("msg"))
            sys.exit(1)
        sys.stdout.flush()

    if not complete_event:
        logger.error("La descarga no emitió el evento 'complete'")
        sys.exit(1)

    # 2. Identificar el PDF descargado
    logger.info("3. Clasificando y localizando archivos resultantes...")
    files = complete_event.get("files", {})
    logger.info("   Resúmenes:   %s", [f.name for f in files.get("resumenes", [])])
    logger.info("   Estudios:    %s", [f.name for f in files.get("estudios", [])])
    logger.info("   Resolutivos: %s", [f.name for f in files.get("resolutivos", [])])

    # Tomar el PDF de estudio si está disponible para probar OCR
    pdf_to_ocr = None
    if files.get("estudios"):
        pdf_to_ocr = files["estudios"][0]
    elif files.get("resumenes"):
        pdf_to_ocr = files["resumenes"][0]
    elif files.get("resolutivos"):
        pdf_to_ocr = files["resolutivos"][0]

    if not pdf_to_ocr:
        logger.error("No se encontró ningún PDF clasificado para aplicar OCR.")
        sys.exit(1)

    # 3. Aplicar OCR y extracción de texto
    logger.info("=" * 70)
    logger.info("4. Ejecutando extracción de texto con OCR Híbrido en %s", pdf_to_ocr.name)
    logger.info("=" * 70)

    start_time = time.time()
    extracted_pages = []
    
    for page_num, total, text, is_scanned in iter_pages_as_markdown(pdf_to_ocr):
        pct = int(page_num / total * 100)
        logger.info(
            "   [Progreso Extracción] %d%%: Página %d/%d (Escaneada: %s, Largo de texto: %d)",
            pct, page_num, total, is_scanned, len(text.strip())
        )
        extracted_pages.append((page_num, text, is_scanned))
        # Mostrar las primeras 3 líneas de texto de la página
        lines = [line.strip() for line in text.split("\n") if line.strip()][:5]
        if lines:
            logger.info("   [Muestra de Texto]:")
            for line in lines:
                logger.info("     > %s", line[:80])
        else:
            logger.warning("   [Muestra de Texto]: Vacio / No se pudo extraer nada")

    end_time = time.time()
    logger.info("=" * 70)
    logger.info("🎉 HARNESS FINALIZADO CON ÉXITO en %.2fs", end_time - start_time)
    logger.info("   Páginas totales extraídas: %d", len(extracted_pages))
    logger.info("=" * 70)

def run_live_batch(n_claves: int = 10):
    logger.info("=" * 70)
    logger.info("🚀 INICIANDO PRUEBA EN VIVO BATCH DE RESILIENCIA DE SCRAPERS (%d CLAVES)", n_claves)
    logger.info("=" * 70)

    import csv
    csv_path = BASE_DIR / "data" / "claves_2026.csv"
    if not csv_path.exists():
        logger.error("No se encontró %s", csv_path)
        sys.exit(1)

    claves = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            c = row.get("CLAVE", "").strip()
            if c:
                claves.append(c)
                if len(claves) >= n_claves:
                    break

    logger.info("Claves seleccionadas para la prueba en vivo: %s", claves)

    harness_dir = BASE_DIR / "downloads"
    downloader = SemarnatDownloader(
        download_dir=harness_dir / "temp_dl",
        headless=True,
        download_timeout=300,
        carpeta_estudios=harness_dir / "estudios",
        carpeta_resumenes=harness_dir / "resumenes",
        carpeta_resolutivos=harness_dir / "resolutivos"
    )

    t0 = time.time()
    results = downloader.batch_desde_lista_concurrent(claves, max_workers=2)
    t1 = time.time()

    successes = [r for r in results if r.get("status") == "complete"]
    failures = [r for r in results if r.get("status") != "complete"]

    logger.info("=" * 70)
    logger.info("📊 RESUMEN DE PRUEBA EN VIVO DE SCRAPER (%d CLAVES):", len(claves))
    logger.info("   - Éxitos: %d / %d (%.1f%%)", len(successes), len(claves), (len(successes)/len(claves))*100)
    logger.info("   - Fallos/Sin archivos: %d", len(failures))
    logger.info("   - Tiempo Total: %.2f segundos", t1 - t0)
    logger.info("=" * 70)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Downloader Test Harness")
    parser.add_argument("--batch", type=int, help="Número de claves reales a procesar en vivo desde claves_2026.csv")
    parser.add_argument("clave", nargs="?", default="05CO2026I0001", help="Clave de prueba")
    parser.add_argument("bitacora", nargs="?", default="09/MG-0006/01/26", help="Bitácora de prueba")
    args = parser.parse_args()

    if args.batch:
        run_live_batch(args.batch)
    else:
        run_test(args.clave, args.bitacora)

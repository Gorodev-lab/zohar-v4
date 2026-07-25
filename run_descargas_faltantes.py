#!/usr/bin/env python3
"""
run_descargas_faltantes.py - FASE B de Zohar v4.

Descarga de forma segura las claves listadas en 'por_hacer.txt' usando el
scraper existente (scrapers/semarnat_downloader.py) con:
  * throttle aleatorio de 3-7s entre claves (anti-bloqueo SEMARNAT)
  * manejo de errores por clave -> 'fallidas.txt' sin detener el script
  * driver Chrome headless reusado durante toda la corrida

NO depende del Dashboard (puerto 8004); es solo descarga.
"""
from __future__ import annotations

import logging
import random
import re
import sys
import time
from pathlib import Path

# Ejecutable desde la raiz del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.semarnat_downloader import SemarnatDownloader

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
POR_HACER = PROJECT_ROOT / "por_hacer.txt"
FALLIDAS = PROJECT_ROOT / "fallidas.txt"
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
LOGS_DIR = PROJECT_ROOT / "logs"
RUN_LOG = LOGS_DIR / "descargas_faltantes.log"

THROTTLE_MIN = 3.0
THROTTLE_MAX = 7.0

# Auto-saner DGIRA (mismo del orquestador original) ----------------------
FIXES = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2'}
LETRA_FIX = {'0': 'O', '1': 'I', '5': 'S'}
MASCARA = "NNLLNNNNLNNNN"
CLAVE_RE = re.compile(r"^[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}$")


def sanar_clave_dgira(clave_sucia: str):
    clave = clave_sucia.strip().upper()
    if len(clave) != 13:
        return None
    out = ""
    for i, ch in enumerate(clave):
        if MASCARA[i] == 'N':
            out += FIXES[ch] if ch in FIXES else ch
        else:
            out += LETRA_FIX[ch] if ch in LETRA_FIX else ch
    return out if CLAVE_RE.match(out) else None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(RUN_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Zohar-FaseB")


def main_with_downloader(downloader, claves: list[str]) -> int:
    """Bucle de descarga sobre un downloader ya construido.

    Extraido de main() para permitir inyeccion de dependencias (tests sin Chrome).
    """
    ok = 0
    fallidas = 0
    total = len(claves)

    try:
        for idx, clave in enumerate(claves, 1):
            logger.info("[%d/%d] Descargando %s ...", idx, total, clave)
            try:
                evento = downloader.descargar_clave(clave)
                status = (evento or {}).get("status", "error")
                if status == "complete":
                    ok += 1
                    logger.info("  OK %s (status=%s)", clave, status)
                else:
                    fallidas += 1
                    with FALLIDAS.open("a", encoding="utf-8") as f:
                        f.write(clave + "\n")
                    logger.warning("  FALLIDA %s (status=%s)", clave, status)
            except Exception as exc:
                fallidas += 1
                with FALLIDAS.open("a", encoding="utf-8") as f:
                    f.write(clave + "\n")
                logger.exception("  EXCEPCION en %s: %s", clave, exc)

            if idx < total:
                espera = random.uniform(THROTTLE_MIN, THROTTLE_MAX)
                logger.info("  throttle %.1fs antes de la siguiente...", espera)
                time.sleep(espera)
    finally:
        try:
            downloader._quit_driver()
        except Exception:
            pass

    logger.info("=" * 60)
    logger.info("FASE B FINALIZADA -> OK=%d  FALLIDAS=%d  TOTAL=%d", ok, fallidas, total)
    if fallidas:
        logger.info("Revisa %s para reintentar las fallidas.", FALLIDAS.name)
    logger.info("=" * 60)
    return 0


def main(downloader=None) -> int:
    if not POR_HACER.exists():
        logger.error("No existe %s. Genera el cruce primero.", POR_HACER)
        return 1

    # Leer y sanear claves
    claves_crudas = [l.strip() for l in POR_HACER.read_text(encoding="utf-8").splitlines() if l.strip()]
    claves = []
    for c in claves_crudas:
        s = sanar_clave_dgira(c)
        if s:
            claves.append(s)
        else:
            logger.warning("Clave descartada (no cumple mascara 13c): %s", c)
    if not claves:
        logger.error("No hay claves validas para descargar.")
        return 1

    logger.info("Iniciando FASE B: %d claves desde %s", len(claves), POR_HACER.name)

    # (Re)iniciar registro de fallidas (append-only durante la corrida)
    FALLIDAS.write_text("", encoding="utf-8")

    if downloader is None:
        downloader = SemarnatDownloader(download_dir=str(DOWNLOAD_DIR), headless=True)
    return main_with_downloader(downloader, claves)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Descarga FASE B de Zohar v4.")
    ap.add_argument("-i", "--input", default=str(POR_HACER),
                    help="Archivo de claves a descargar (def: por_hacer.txt)")
    ap.add_argument("--throttle-min", type=float, default=THROTTLE_MIN,
                    help="Segundos minimos de espera entre claves")
    ap.add_argument("--throttle-max", type=float, default=THROTTLE_MAX,
                    help="Segundos maximos de espera entre claves")
    args = ap.parse_args()
    POR_HACER = Path(args.input)
    THROTTLE_MIN = args.throttle_min
    THROTTLE_MAX = args.throttle_max
    sys.exit(main())

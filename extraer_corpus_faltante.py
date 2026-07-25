#!/usr/bin/env python3
"""
extraer_corpus_faltante.py - FASE A de Zohar v4 (extraccion PURA, sin LLM).

Procesa los PDFs de 'downloads/' y genera, para cada uno que pase el
gate de integridad del verifier:
  * extractions/<CLAVE>.<tipo>.<nn>.md      -> texto del PDF en Markdown
    (+ seccion de bloques GEO/LAW/BIO detectados por regex, sin LLM)
  * extractions/<CLAVE>.<tipo>.<nn>.meta.json -> metadatos locales
    (paginas, sha256, tamaño, conteo de bloques detectados)

NO usa StructuredExtractor ni inferencia: 0 LLM, 0 red, 0 Dashboard.
Reutiliza core.pdf_processor.iter_pages_as_markdown (que ya aplica
PDFDownloadVerifier como pre-gate y RapidOCR en paginas escaneadas).

Diseno:
  - Lotes de N PDFs (def. 20) con throttle ligero 0.2-1s entre PDFs.
  - Idempotente: si el .md ya existe en extractions/, se salta.
  - PDFs bloqueados por el verifier -> omitidos.txt (no detiene el script).
  - Logs a logs/extraer_corpus.log + stdout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.pdf_processor import (
    iter_pages_as_markdown,
    classify_page,
)

# Regex anclado de clave SINAT (13c) - mismo que usa SecondBrainBuilder
CLAVE_RE = re.compile(r"^(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})$")

# Sufijo tipo del PDF: "02BC2024E0044.resolutivo.00.pdf" -> "resolutivo.00"
SUFFIX_RE = re.compile(r"^\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5}\.(.+)\.pdf$", re.IGNORECASE)

DOWNLOADS = PROJECT_ROOT / "downloads"
EXTRACTIONS = PROJECT_ROOT / "extractions"
LOGS_DIR = PROJECT_ROOT / "logs"
RUN_LOG = LOGS_DIR / "extraer_corpus.log"
OMITIDOS = PROJECT_ROOT / "omitidos.txt"

LOTE = 20
THROTTLE_MIN = 0.2
THROTTLE_MAX = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(RUN_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Zohar-FaseA")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_pdf_name(pdf_path: Path):
    """Devuelve (clave, suffix) o (None, None) si no matchea la mascara."""
    stem = pdf_path.name
    m = CLAVE_RE.match(pdf_path.stem)  # stem = nombre sin .pdf
    if not m:
        # Intenta con SUFFIX_RE sobre el nombre completo por si trae punto extra
        ms = SUFFIX_RE.match(stem)
        if ms:
            clave = stem.split(".")[0]
            return clave, ms.group(1)
        return None, None
    # stem ya es la clave (sin puntos extra)
    return m.group(1), ""


def process_pdf(pdf_path: Path) -> str:
    """
    Procesa un PDF. Retorna uno de: 'ok', 'skip', 'omitido', 'error'.
    """
    clave, suffix = parse_pdf_name(pdf_path)
    if not clave:
        logger.warning("  Nombre no cumple mascara 13c: %s (skip)", pdf_path.name)
        return "skip"

    # Nombre de salida espejo: CLAVE.suffix.md  (si no hay suffix, solo CLAVE.md)
    out_name = f"{clave}.{suffix}.md" if suffix else f"{clave}.md"
    out_md = EXTRACTIONS / out_name
    out_meta = EXTRACTIONS / (out_md.stem + ".meta.json")

    if out_md.exists():
        logger.info("  SKIP (ya existe): %s", out_md.name)
        return "skip"

    # Acumular Markdown de todas las paginas + detectar bloques
    pages_md = []
    blocks = {"geo": set(), "law": set(), "bio": set()}
    n_pages = 0
    scanned_pages = 0
    try:
        for page_num, total_pages, md_text, is_scanned in iter_pages_as_markdown(pdf_path):
            n_pages = total_pages
            if is_scanned:
                scanned_pages += 1
            pages_md.append(md_text)
            cls = classify_page(md_text)
            for k in ("geo", "law", "bio"):
                blocks[k].update(cls[k])
    except Exception as exc:
        logger.exception("  ERROR extrayendo %s: %s", pdf_path.name, exc)
        return "error"

    if not pages_md:
        logger.warning("  SIN TEXTO util (verifier bloqueo?): %s", pdf_path.name)
        # iter_pages_as_markdown retorna generator vacio si el verifier bloquea
        with OMITIDOS.open("a", encoding="utf-8") as f:
            f.write(f"{clave}\t{pdf_path.name}\tomitido: verifier bloqueo o sin texto util\n")
        return "omitido"

    # Escribir Markdown
    header = (
        "---\n"
        f"clave: {clave}\n"
        f"fuente_pdf: {pdf_path.name}\n"
        f"paginas: {n_pages}\n"
        f"paginas_escaneadas_ocr: {scanned_pages}\n"
        "---\n\n"
        "## Contenido extraido (Markdown)\n\n"
    )
    bloques_md = (
        "\n\n---\n\n## Bloques detectados (regex, sin LLM)\n\n"
        f"### GEO ({len(blocks['geo'])})\n"
        + "\n".join(f"- {l}" for l in sorted(blocks["geo"]))
        + "\n"
        f"\n### LAW ({len(blocks['law'])})\n"
        + "\n".join(f"- {l}" for l in sorted(blocks["law"]))
        + "\n"
        f"\n### BIO ({len(blocks['bio'])})\n"
        + "\n".join(f"- {l}" for l in sorted(blocks["bio"]))
        + "\n"
    )

    out_md.write_text(header + "\n\n".join(pages_md) + bloques_md, encoding="utf-8")

    # Metadatos JSON (sin LLM)
    meta = {
        "clave": clave,
        "fuente_pdf": pdf_path.name,
        "paginas": n_pages,
        "paginas_ocr": scanned_pages,
        "sha256": sha256_of(pdf_path),
        "bloques": {k: sorted(v) for k, v in blocks.items()},
        "generado_por": "extraer_corpus_faltante.py (FASE A, sin LLM)",
    }
    out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("  OK -> %s (+meta.json) [%d pag, %d OCR]", out_md.name, n_pages, scanned_pages)
    return "ok"


def main(lote_max: int = 0, checkpoint: str = "") -> int:
    EXTRACTIONS.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Reiniciar registro de omitidos (append-only durante la corrida)
    OMITIDOS.write_text("", encoding="utf-8")

    # 1) Recolectar PDFs validos por nombre
    pdfs = []
    for p in sorted(DOWNLOADS.rglob("*.pdf")):
        clave, _ = parse_pdf_name(p)
        if clave:
            pdfs.append(p)
    logger.info("PDFs con clave 13c en downloads/: %d", len(pdfs))
    if not pdfs:
        logger.error("No hay PDFs que procesar.")
        return 1

    # Checkpoint: si se pasa un archivo, solo procesa las claves listadas ahi
    if checkpoint:
        try:
            want = set(l.strip() for l in open(checkpoint) if l.strip())
            pdfs = [p for p in pdfs if parse_pdf_name(p)[0] in want]
            logger.info("Filtrado por checkpoint %s -> %d PDFs", checkpoint, len(pdfs))
        except Exception as exc:
            logger.warning("No se pudo leer checkpoint %s: %s", checkpoint, exc)

    ok = skip = omit = err = 0
    total = len(pdfs)
    lote_actual = 0
    procesados = 0

    for idx, pdf_path in enumerate(pdfs, 1):
        logger.info("[%d/%d] %s", idx, total, pdf_path.name)
        try:
            res = process_pdf(pdf_path)
        except Exception as exc:  # nunca detener el script
            logger.exception("  EXCEPCION en %s: %s", pdf_path.name, exc)
            res = "error"

        if res == "ok":
            ok += 1
        elif res == "skip":
            skip += 1
        elif res == "omitido":
            omit += 1
        else:
            err += 1

        lote_actual += 1
        procesados += 1
        # Throttle cada LOTE pdfs (no en el ultimo)
        if lote_actual >= LOTE and idx < total:
            lote_actual = 0
            espera = random.uniform(THROTTLE_MIN, THROTTLE_MAX)
            logger.info("  -- lote de %d completado, throttle %.2fs --", LOTE, espera)
            time.sleep(espera)
        elif idx < total:
            # throttle ligero entre cada PDF
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))

        # Corte por lote maximo (para relanzar en lotes controlados)
        if lote_max and procesados >= lote_max:
            logger.info("  -- límite de lote (%d) alcanzado, saliendo para relanzar --", lote_max)
            break

    logger.info("=" * 60)
    logger.info("FASE A FINALIZADA -> OK=%d  SKIP=%d  OMIT=%d  ERR=%d  TOTAL=%d",
                ok, skip, omit, err, total)
    logger.info("Markdown en: %s | omitidos en: %s", EXTRACTIONS.name, OMITIDOS.name)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Extraccion FASE A de Zohar v4.")
    ap.add_argument("--lote-max", type=int, default=0,
                    help="Maximo de PDFs a procesar antes de salir (0=sin limite)")
    ap.add_argument("--checkpoint", default="",
                    help="Archivo con claves a procesar (filtra downloads/)")
    ap.add_argument("--throttle-min", type=float, default=THROTTLE_MIN)
    ap.add_argument("--throttle-max", type=float, default=THROTTLE_MAX)
    args = ap.parse_args()
    THROTTLE_MIN = args.throttle_min
    THROTTLE_MAX = args.throttle_max
    sys.exit(main(lote_max=args.lote_max, checkpoint=args.checkpoint))

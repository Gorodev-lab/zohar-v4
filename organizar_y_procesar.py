#!/usr/bin/env python3
"""
organizar_y_procesar.py — Zohar v4

1. Organiza los PDFs del corpus en carpetas por clave:
       downloads/<CLAVE>/<CLAVE>.<tipo>.NN.pdf
   (tipo = resumen | estudio | resolutivo | gaceta; se infiere del nombre).
2. Por cada clave, extrae su contenido a Markdown en extractions/<CLAVE>.md
   usando core.pdf_processor.iter_pages_as_markdown, que YA aplica OCR
   (RapidOCR) SOLO cuando la pagina tiene poco texto digital
   (pagina escaneada). No se fuerza OCR en documentos digitales.
3. Analiza cada proyecto con el modelo local (gemma primario, Mistral
   fallback) via core.inference_engine.generate_report. La inferencia usa
   SOLO el resumen (extractions/<CLAVE>.resumen.md) como entrada, acotado a
   ~6k chars (head+tail) para no reventar el contexto del llama-server. El
   reporte se guarda en data/inference_cache/<CLAVE>.json. Con --full tambien
   se extrae estudio/resolutivo a <CLAVE>.md (OCR pesado si son escaneados).

Uso la arquitectura existente; no se inventa nada.

Uso:
  python organizar_y_procesar.py --dry-run        # solo muestra lo que harias
  python organizar_y_procesar.py                 # ejecuta organizacion + extraccion + inferencia
  python organizar_y_procesar.py --limit 5          # solo las primeras N claves
  python organizar_y_procesar.py --no-inference     # solo organizar + extraer a MD

El script es idempotente: no re-extrae claves que ya tienen .md y no
re-infiere claves que ya tienen .json en inference_cache (salvo --force).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("organizar")

BASE = Path(__file__).parent
DOWNLOADS = BASE / "downloads"
EXTRACTIONS = BASE / "extractions"
INFERENCE = BASE / "data" / "inference_cache"

# Patron de clave SINAT/bitacora en el nombre del archivo.
CLAVE_RE = re.compile(r"(?P<clave>\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})", re.I)
TIPO_RE = re.compile(r"\.(?P<tipo>resumen|estudio|resolutivo|gaceta)\.", re.I)


def inferir_clave_y_tipo(nombre: str) -> tuple[str | None, str]:
    """Devuelve (clave_o_None, tipo). Solo claves SINAT validas (13-15 chars)."""
    m = CLAVE_RE.search(nombre)
    clave = m.group("clave").upper() if m else None
    mt = TIPO_RE.search(nombre)
    tipo = mt.group("tipo").lower() if mt else "otro"
    return clave, tipo


def descubrir_pdfs() -> list[Path]:
    """Todos los PDFs bajo downloads/ (raiz + subdirs), sin duplicados por nombre."""
    encontrados: dict[str, Path] = {}
    for p in DOWNLOADS.rglob("*.pdf"):
        # Ignorar los que ya estan dentro de una carpeta <clave>/ (ya organizados)
        if p.parent != DOWNLOADS and re.fullmatch(r"[0-9A-Z]{13,15}", p.parent.name, re.I):
            continue
        encontrados[p.name] = p
    return list(encontrados.values())


def organizar(pdfs: list[Path], dry: bool) -> dict[str, list[Path]]:
    """Mueve cada PDF a downloads/<CLAVE>/<nombre> si tiene clave valida,
    o a downloads/sin_clave/<nombre> si no. Devuelve clave -> [pdfs]."""
    por_clave: dict[str, list[Path]] = {}
    for pdf in pdfs:
        clave, _tipo = inferir_clave_y_tipo(pdf.name)
        carpeta = clave if clave else "sin_clave"
        dest_dir = DOWNLOADS / carpeta
        dest = dest_dir / pdf.name
        if dest.exists():
            por_clave.setdefault(carpeta, []).append(dest)
            continue
        if dry:
            logger.info("[DRY] mover %s -> %s", pdf.name, dest)
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(dest))
            logger.info("movido %s -> %s", pdf.name, dest)
        por_clave.setdefault(carpeta, []).append(dest)
    return por_clave


def _extraer_paginas(pdfs, clave, dry):
    """Extrae una lista de PDFs a markdown (OCR condicional). Devuelve bloques."""
    from core.pdf_processor import iter_pages_as_markdown
    paginas_md = []
    for pdf in sorted(pdfs, key=lambda p: p.name):
        logger.info("%s: extrayendo %s", clave, pdf.name)
        if dry:
            paginas_md.append(f"<!-- DRY: {pdf.name} -->")
            continue
        try:
            for _page, _total, md_text, is_scanned in iter_pages_as_markdown(pdf):
                tag = " [OCR]" if is_scanned else ""
                paginas_md.append(f"{md_text}{tag}")
        except Exception as exc:
            logger.warning("%s: error extrayendo %s: %s", clave, pdf.name, exc)
    return paginas_md


def _escribir_md(path, clave, paginas, dry, sufijo):
    contenido = f"# {clave} {sufijo}\n\n" + "\n\n---\n\n".join(paginas) + "\n"
    if not dry:
        EXTRACTIONS.mkdir(parents=True, exist_ok=True)
        path.write_text(contenido, encoding="utf-8")
        logger.info("%s: %s escrito (%d chars)", clave, path.name, len(contenido))
    else:
        logger.info("[DRY] escribir %s (%d chars)", path, len(contenido))
    return path


def extraer_clave(clave: str, pdfs: list[Path], dry: bool, full: bool = False):
    """Extrae el corpus de una clave a Markdown (OCR condicional).

    SIEMPRE extrae el resumen -> extractions/<CLAVE>.resumen.md (entrada rapida
    de inferencia, suele ser pequeno). Si full=True, tambien extrae
    estudio/resolutivo a extractions/<CLAVE>.md (mas lento, para consulta profunda).

    Devuelve (resumen_md_path, full_md_path_o_None).
    El fd 2 ya esta redirigido a /dev/null en main(), asi que el ruido de
    pymupdf/RapidOCR no ensucia el log.
    """
    resumen_pdfs, otros = [], []
    for pdf in pdfs:
        _, tipo = inferir_clave_y_tipo(pdf.name)
        (resumen_pdfs if tipo == "resumen" else otros).append(pdf)

    resumen_md = EXTRACTIONS / f"{clave}.resumen.md"
    full_md = EXTRACTIONS / f"{clave}.md"

    # Resumen: siempre (es la entrada de inferencia, suele ser pequeno).
    if resumen_md.exists() and not dry:
        logger.info("%s: resumen.md ya existe, omitiendo extraccion de resumen", clave)
    else:
        paginas = _extraer_paginas(resumen_pdfs, clave, dry)
        if paginas or dry:
            resumen_md = _escribir_md(resumen_md, clave, paginas, dry, "[resumen]")

    # Completo (estudio/resolutivo): solo si --full (puede ser enorme/OCR lento).
    full_path = None
    if full:
        if full_md.exists() and not dry:
            logger.info("%s: .md completo ya existe, omitiendo", clave)
            full_path = full_md
        else:
            paginas = _extraer_paginas(otros, clave, dry)
            if paginas or dry:
                full_path = _escribir_md(full_md, clave, paginas, dry, "[completo]")

    return resumen_md, full_path


def analizar_clave(clave: str, md_path: Path, dry: bool, force: bool) -> dict | None:
    """Analiza el .md con generate_report (local gemma primario, Mistral fallback).

    generate_report acota el contexto a ~1500 chars SOLO si prefer_local=True;
    si el health-check elige otro backend, manda el .md completo y puede reventar
    el contexto del llama-server (400). Para garantizar robustez, acotamos el
    archivo de inferencia a MAX_INFER_CHARS antes de llamar.
    """
    from core.inference_engine import generate_report

    out_path = INFERENCE / f"{clave}.json"
    if out_path.exists() and not force and not dry:
        logger.info("%s: inferencia ya existe, omitiendo", clave)
        return None

    if dry:
        logger.info("[DRY] inferencia para %s", clave)
        return None
    if md_path is None or not md_path.exists():
        logger.warning("%s: sin .md para inferencia", clave)
        return None

    # Acotar entrada de inferencia para que el llama-server local (gemma en CPU)
    # termine en <20s y no dispare el limite de ~40s del server (que cancela y
    # reinicia la generacion). 700 chars + n_predict=100 => ~18s, margen seguro.
    # Truncacion inteligente: head (alcance/objeto) + tail (resolucion/condicionantes).
    MAX_INFER_CHARS = 700
    HEAD = 300
    TAIL = 400
    target = md_path
    try:
        size = md_path.stat().st_size
        if size > MAX_INFER_CHARS:
            capped = md_path.with_suffix("").with_name(md_path.stem + ".infer.md")
            texto = md_path.read_text(encoding="utf-8", errors="replace")
            if len(texto) > MAX_INFER_CHARS:
                texto = texto[:HEAD] + "\n\n...[medio omitido]...\n\n" + texto[-TAIL:]
            capped.write_text(texto, encoding="utf-8")
            target = capped
            logger.info("%s: resumen grande (%d chars) acotado a %d (head+tail) para inferencia",
                        clave, size, MAX_INFER_CHARS)
    except Exception as exc:
        logger.warning("%s: no se pudo acotar el resumen: %s", clave, exc)

    logger.info("%s: generando reporte de inferencia (modelo local gemma, prefer_local=True)...", clave)

    # Prime por clave: el llama-server (gemma en CPU) desconecta la PRIMERA
    # generacion tras quedar idle. Hacemos un llamado descartable para dejarlo
    # caliente justo antes de la generacion real (evita el ciclo
    # desconexion->prime->reintento que multiplica x3 el tiempo por clave).
    if not dry:
        try:
            from core.llm_client import generate_completion as _gc
            _gc(prompt="OK", system_prompt="responde OK", response_json=False,
                n_predict=8, prefer_local=True)
        except Exception:
            pass

    try:
        reporte = generate_report(target, prefer_local=True)
        INFERENCE.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            __import__("json").dumps(reporte, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        veredicto = reporte.get("veredicto", "?")
        logger.info("%s: reporte OK -> %s", clave, veredicto)
        return reporte
    except Exception as exc:
        logger.error("%s: error en inferencia: %s", clave, exc)
        return None


def main():
    ap = argparse.ArgumentParser(description="Organiza y procesa el corpus Zohar v4 por clave.")
    ap.add_argument("--dry-run", action="store_true", help="No mover ni escribir; solo reporta.")
    ap.add_argument("--no-move", action="store_true",
                        help="No reubicar PDFs fisicamente; dejarlos en downloads/ y solo "
                             "agrupar extraccion+inferencia por clave (respetando el backend).")
    ap.add_argument("--limit", type=int, default=0, help="Procesar solo las primeras N claves.")
    ap.add_argument("--no-inference", action="store_true", help="Solo organizar y extraer a MD.")
    ap.add_argument("--full", action="store_true",
                        help="Tambien extrae estudio/resolutivo (OCR pesado si son escaneados). "
                             "Por defecto solo el resumen alimenta la inferencia.")
    ap.add_argument("--force", action="store_true", help="Re-infiere claves ya procesadas.")
    ap.add_argument("--claves", type=str, default="",
                        help="Solo procesar estas claves, separadas por espacio o coma.")
    args = ap.parse_args()

    # pymupdf/RapidOCR (y su logger) escriben null-bytes y warnings al fd 2.
    # Redirigimos el fd 2 completo a /dev/null a nivel de descriptor, no solo
    # el objeto sys.stderr, para atrapar tambien los handlers de loggers de
    # terceros que ya capturaron el stderr original. Mi logger usa stdout (fd 1),
    # asi que queda legible en el log.
    _devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_devnull_fd, 2)

    dry = args.dry_run
    logger.info("=== INICIO %s ===", "DRY-RUN" if dry else ("EJECUCION" + (" [no-move]" if args.no_move else "")))

    pdfs = descubrir_pdfs()
    logger.info("PDFs descubiertos en downloads/: %d", len(pdfs))

    if args.no_move:
        # Agrupar en memoria por clave sin tocar el disco de PDFs.
        por_clave = {}
        for pdf in pdfs:
            clave, _ = inferir_clave_y_tipo(pdf.name)
            por_clave.setdefault(clave or "sin_clave", []).append(pdf)
        logger.info("Claves agrupadas (sin mover): %d", len(por_clave))
    else:
        por_clave = organizar(pdfs, dry)
        logger.info("Claves unicas agrupadas: %d", len(por_clave))

    claves = sorted(por_clave.keys())
    if args.claves.strip():
        sel = {c.strip() for c in re.split(r"[ ,]+", args.claves) if c.strip()}
        claves = [c for c in claves if c in sel]
        logger.info("Filtro --claves: %d claves seleccionadas", len(claves))
    if args.limit:
        claves = claves[: args.limit]
        logger.info("Limite de claves: %d", len(claves))

    # Warm-up del llama-server local: la PRIMERA generacion "pesada" tras
    # arrancar el server SIEMPRE desconecta (cold-start glitch del GGUF/gemma
    # en CPU). Hacemos UN llamado real (prompt + n_predict grandes) y
    # descartamos su resultado; a partir de ahi el server queda estable y
    # todas las generaciones reales del lote funcionan.
    if not args.no_inference:
        try:
            from core.llm_client import generate_completion
            logger.info("Warm-up llama-server (1er llamado pesado descartado)...")
            try:
                generate_completion(
                    prompt="Proyecto de infraestructura. Evalua impacto ambiental y responde en JSON con veredicto y condicionantes." * 3,
                    system_prompt="Eres analista. Responde JSON.",
                    response_json=True,
                    n_predict=100,
                    prefer_local=True,
                )
            except Exception:
                pass  # el resultado no importa; solo primar el server
            logger.info("Warm-up completado.")
        except Exception as e:
            logger.warning("Warm-up fallido (se reintentara por clave): %s", e)

    exitos = 0
    fallos = 0
    t0 = time.time()
    for i, clave in enumerate(claves, 1):
        logger.info("--- [%d/%d] CLAVE %s ---", i, len(claves), clave)
        resumen_md, full_md = extraer_clave(clave, por_clave[clave], dry, args.full)
        if args.no_inference:
            if resumen_md:
                exitos += 1
            continue
        rep = analizar_clave(clave, resumen_md, dry, args.force)
        if rep is not None or (resumen_md and dry):
            exitos += 1
        elif resumen_md is None:
            fallos += 1

    dt = time.time() - t0
    logger.info("=== FIN: %d claves, %d exitos, %d fallos, %.1fs ===",
                 len(claves), exitos, fallos, dt)


if __name__ == "__main__":
    main()

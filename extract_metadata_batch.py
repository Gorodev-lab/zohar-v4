#!/usr/bin/env python3
"""
extract_metadata_batch.py - Runner del Bloque 2 para el extractor de metadata.

FLUJO (una sola llamada LLM por documento):
  1. Cargar .md de extracción de la clave.
  2. locate_metadata_snippets()  -> contexto crudo (sin LLM).
  3. build_extraction_prompt()   -> prompt JSON estricto.
  4. call_llm()                  -> respuesta JSON del LLM local (Gemma).
  5. validate_extraction()       -> reglas del punto 5.
  6. upsert_metadata()           -> escribe a SQLite (o DRY_RUN solo log).

ESTADO: listo para correr, PERO no se ejecuta hasta que el usuario confirme
que FASE A (OCR de las 108) liberó el llama-server. Por defecto DRY_RUN=True
(nunca escribe a la DB real; solo imprime/registra).

Invocación prevista ( Bloque 2 manual ):
  python3 extract_metadata_batch.py --claves 04CA2026E0011 03BS2024U0025 02BC2024E0044 02BC2025E0049 --dry-run
  python3 extract_metadata_batch.py --backfill --dry-run
  python3 extract_metadata_batch.py --backfill            # escritura real (tras aprobacion)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.metadata_extractor import (
    locate_metadata_snippets, build_extraction_prompt,
    validate_extraction, upsert_metadata, init_db,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("metadata-batch")

EXTRACTIONS = Path(__file__).resolve().parent / "extractions"
CLAVE_RE = __import__("re").compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")

# Contexto para el LLM: forzar Gemma local (modo offline, sin Mistral).
LLM_SYSTEM_PROMPT = (
    "Eres un extractor de metadatos de MIA mexicanas. Responde SOLO JSON valido, "
    "sin markdown, sin texto extra. Extrae unico y exclusivamente lo literal del "
    "texto; nunca infieras ni completes con conocimiento externo."
)


def call_llm(prompt: str) -> dict | None:
    """
    Llamada REAL al LLM local (Gemma via llama-server :8083).
    Requiere que FASE A haya liberado el llama-server.
    Devuelve el dict parseado o None si falla.
    """
    from core.llm_client import generate_completion
    try:
        resp = generate_completion(
            prompt,
            system_prompt=LLM_SYSTEM_PROMPT,
            response_json=True,
            n_predict=512,
            prefer_local=True,
        )
        if isinstance(resp, dict) and "content" in resp:
            text = resp["content"]
        elif isinstance(resp, str):
            text = resp
        else:
            text = json.dumps(resp)
        # El LLM puede devolver JSON embebido en markdown; limpiar
        text = text.strip().strip("`").replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as exc:
        logger.error("Fallo LLM: %s", exc)
        return None


def procesar_clave(clave: str, dry_run: bool = True) -> dict:
    """Pipeline completo para una clave. Devuelve el resultado del upsert/validacion."""
    # Regla estricta de selección de archivos:
    # 1. Intentar cargar {clave}.resumen.00.md
    # 2. Si no existe, fallback a {clave}.md
    # 3. Si tampoco existe, registrar warning y retornar "sin .md"
    md_path_resumen = EXTRACTIONS / f"{clave}.resumen.00.md"
    md_path_base = EXTRACTIONS / f"{clave}.md"
    
    if md_path_resumen.exists():
        md_path = md_path_resumen
    elif md_path_base.exists():
        md_path = md_path_base
    else:
        logger.warning("[%s] sin .md de extraccion", clave)
        return {"clave": clave, "escrito": False, "razon": "sin .md"}
    
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    snippets = locate_metadata_snippets(text)
    prompt = build_extraction_prompt(snippets)

    data = call_llm(prompt)
    if data is None:
        return {"clave": clave, "escrito": False, "razon": "LLM sin respuesta"}

    val = validate_extraction(clave, data)
    if dry_run:
        logger.info("[DRY-RUN %s] validacion=%s rev=%s data=%s",
                    clave, val["nivel"], val["requiere_revision"], json.dumps(data, ensure_ascii=False)[:200])
        return {"clave": clave, "dry_run": True, "validacion": val, "data": data}

    resultado = upsert_metadata(clave, data, snippet_fuente=snippets.get("datos_generales", ""))
    return resultado


def main():
    ap = argparse.ArgumentParser(description="Extractor de metadata MIA (Bloque 2)")
    ap.add_argument("--claves", nargs="*", help="Claves SINAT especificas a procesar")
    ap.add_argument("--backfill", action="store_true", help="Procesar todas las claves con .md")
    ap.add_argument("--dry-run", action="store_true", help="No escribir a SQLite (solo log)")
    ap.add_argument("--db", default=None, help="Ruta SQLite (def: data/metadata_proyecto.db)")
    args = ap.parse_args()

    dry_run = args.dry_run
    if not dry_run:
        logger.warning("MODO ESCRITURA REAL (dry_run=False). Requiere llama-server libre.")
        init_db(args.db)

    if args.backfill:
        claves = sorted({CLAVE_RE.match(f.name).group(1) for f in EXTRACTIONS.glob("*.md")
                         if CLAVE_RE.match(f.name)})
    else:
        claves = [c.upper() for c in (args.claves or [])]

    logger.info("Procesando %d claves (dry_run=%s)", len(claves), dry_run)
    for c in claves:
        procesar_clave(c, dry_run=dry_run)


if __name__ == "__main__":
    main()

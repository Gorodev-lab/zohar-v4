#!/usr/bin/env python3
"""
pdf_summarize.py
================
Script CLI para procesar resúmenes de PDFs por Map-Reduce y extracción JSON.
Optimizado para hardware AMD Ryzen 5 (CPU inferencia local en :8083).

Uso:
    python3 pdf_summarize.py [--limit 1] [--max-chunks 3]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from core.config import PROJECT_ROOT
from core.pdf_summarizer import summarize_pdf_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zohar v4 — Procesador Map-Reduce & Metadatos JSON de PDFs")
    parser.add_argument("--limit", type=int, default=1, help="Número de PDFs a procesar (0 para todos)")
    parser.add_argument("--all", action="store_true", help="Procesar todos los PDFs encontrados sin límite")
    parser.add_argument("--max-chunks", type=int, default=3, help="Máximo número de bloques Map por PDF")
    args = parser.parse_args()

    search_dirs = [
        PROJECT_ROOT / "corpus_pdf",
        PROJECT_ROOT / "downloads" / "resumenes",
        PROJECT_ROOT / "downloads" / "gacetas",
        PROJECT_ROOT / "downloads"
    ]
    
    pdf_files = []
    seen = set()
    for d in search_dirs:
        if d.exists():
            for found in d.glob("*.pdf"):
                if found.resolve() not in seen:
                    seen.add(found.resolve())
                    pdf_files.append(found)

    if not args.all and args.limit > 0:
        pdf_files = pdf_files[:args.limit]

    if not pdf_files:
        print("[INFO] No se encontraron archivos .pdf en downloads/ o corpus_pdf/.")
        sys.exit(0)

    print(f"\n========================================================")
    print(f" ZOHAR v4 — PROCESADOR LOCAL DE PDFs ({len(pdf_files)} archivo(s))")
    print(f"========================================================\n")

    for idx, pdf in enumerate(pdf_files, 1):
        print(f"[{idx}/{len(pdf_files)}] 📄 Procesando: {pdf.name}")
        print(f"    Ubicación: {pdf}")
        print(f"    Configuración: max_chunks={args.max_chunks}")
        print("--------------------------------------------------------")
        
        res = summarize_pdf_file(pdf, max_chunks=args.max_chunks)

        print(f"\n✅ RESULTADO:")
        print(f"   • Estatus: {res.get('status')}")
        print(f"   • Páginas Totales: {res.get('total_pages')}")
        print(f"   • Páginas Imagen/Escaneadas: {res.get('scanned_pages', 0)}")
        print(f"   • Bloques Map Procesados: {res.get('chunk_count')}")
        print(f"   • Tiempo Transcurrido: {res.get('elapsed_seconds')}s")
        print(f"   • Indexado Semántico: {'Sí' if res.get('semantic_indexed') else 'No/Sin cambios'}")
        print(f"   • Nota Second Brain: {res.get('note_path')}")
        
        meta = res.get("metadata_json", {})
        if meta:
            print(f"   • Metadatos JSON Extraídos:")
            print(json.dumps(meta, indent=6, ensure_ascii=False))

        print("========================================================\n")

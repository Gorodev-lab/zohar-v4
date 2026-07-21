#!/usr/bin/env python3
"""
pdf_summarize.py
================
Script CLI para procesar resúmenes de PDFs por Map-Reduce optimizado para Ryzen 5.
Uso:
    python3 pdf_summarize.py [--limit 1] [--max-chunks 3]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from core.config import PROJECT_ROOT
from core.pdf_summarizer import summarize_pdf_file

CORPUS_PDF_DIR = PROJECT_ROOT / "corpus_pdf"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zohar v4 — Procesador Map-Reduce de PDFs")
    parser.add_argument("--limit", type=int, default=1, help="Número de PDFs a procesar del corpus_pdf/")
    parser.add_argument("--max-chunks", type=int, default=3, help="Máximo número de bloques por PDF")
    args = parser.parse_args()

    pdf_files = []
    search_dirs = [
        PROJECT_ROOT / "downloads" / "resumenes",
        PROJECT_ROOT / "downloads" / "gacetas",
        PROJECT_ROOT / "corpus_pdf"
    ]
    for d in search_dirs:
        if d.exists():
            pdf_files.extend(list(d.glob("*.pdf")))

    pdf_files = pdf_files[:args.limit]
    if not pdf_files:
        print("[INFO] No se encontraron archivos .pdf en downloads/ o corpus_pdf/.")
        exit(0)

    for pdf in pdf_files:
        print(f"[START] Procesando {pdf.name} (max_chunks={args.max_chunks})...")
        res = summarize_pdf_file(pdf, max_chunks=args.max_chunks)
        print(f"[STATUS] {res.get('status')}")
        print(f"[PAGES] Páginas: {res.get('total_pages')}, Bloques procesados: {res.get('chunk_count')}")
        print(f"[TIME] Tiempo: {res.get('elapsed_seconds')}s")
        print(f"[SECOND BRAIN] Nota guardada en: {res.get('note_path')}")

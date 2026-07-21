#!/usr/bin/env python3
"""
dw_ingest.py
============
Script CLI para ejecutar la tubería mínima efectiva de ingesta hacia la Base de Datos PostgreSQL.
Uso:
    python3 dw_ingest.py [--limit 10]
"""

from __future__ import annotations

import argparse
import sys
from core.dw_pipeline import run_incremental_ingest, get_db_stats

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zohar v4 — Ingesta Mínima a PostgreSQL")
    parser.add_argument("--limit", type=int, default=10, help="Límite de expedientes a procesar")
    args = parser.parse_args()

    print("[START] Ejecutando tubería mínima efectiva de ingesta...")
    res = run_incremental_ingest(limit=args.limit)
    print(f"[STATUS] {res.get('status')}")
    print(f"[PROCESSED] Procesados: {res.get('processed')}, Insertados en BD: {res.get('inserted')}")
    print(f"[TIME] Tiempo de ejecución: {res.get('elapsed_seconds')}s")

    stats = get_db_stats()
    print(f"[POSTGRES DB] Total expedientes en BD: {stats.get('total_proyectos')}, Total promoventes: {stats.get('total_promoventes')}")

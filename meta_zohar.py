#!/usr/bin/env python3
"""
meta_zohar.py
=============
Wrapper de retrocompatibilidad para Zohar v4.

Reencausa las invocaciones legacy de `meta_zohar.py` hacia el nuevo motor RSI
genérico (`auto_improver.py`) configurado para el objetivo `infer.py`::`extract_entities`.

Uso:
    ./venv/bin/python meta_zohar.py [--iterations N] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from core.config import PYTHON_EXE, PROJECT_ROOT
from auto_improver import run_rsi

def main():
    parser = argparse.ArgumentParser(
        description="Zohar v4 — Meta-Optimizador RSI para infer.py (Wrapper de retrocompatibilidad)"
    )
    parser.add_argument(
        "--iterations", "-i",
        type=int,
        default=3,
        help="Número de iteraciones del bucle RSI (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sin aplicar parches",
    )
    args = parser.parse_args()

    print("=== REDIRIGIENDO AL MOTOR RSI GENÉRICO (OBJETIVO: infer.py) ===")
    run_rsi(
        max_cycles=args.iterations,
        dry_run=args.dry_run,
        target_file="infer.py",
        func_name="extract_entities",
        eval_cmd=f"{PYTHON_EXE} eval_zohar.py",
        eval_metric="score_float",
        patch_anchors=["PROMPT DE EXTRACCIÓN", "prompt =", "try:"],
        max_window=80,
    )

if __name__ == "__main__":
    main()

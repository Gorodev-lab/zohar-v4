#!/usr/bin/env python3
"""
rsi_run_all.py
==============
Orquestador multi-objetivo del Motor RSI de Zohar v4.

Lee el grafo de graphify (graphify-out/graph.json) para obtener el
betweenness centrality de cada función objetivo, ordena los targets
de mayor a menor criticidad, y ejecuta el RSI sobre cada uno.

Uso:
    ./venv/bin/python rsi_run_all.py --cycles-per-target 2 --dry-run
    ./venv/bin/python rsi_run_all.py --cycles-per-target 1
    ./venv/bin/python rsi_run_all.py --only infer.py  # solo un objetivo específico
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Importar el motor RSI genérico y el registro de objetivos
from auto_improver import run_rsi, get_graphify_betweenness, GRAPHIFY_GRAPH
from core.rsi_targets import RSI_TARGETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rsi_run_all")


# ---------------------------------------------------------------------------
# Priorización por betweenness centrality (graphify)
# ---------------------------------------------------------------------------

def prioritize_targets(targets: list[dict]) -> list[dict]:
    """
    Ordena los targets de mayor a menor betweenness centrality según graphify.
    Si un nodo no se encuentra en el grafo, se le asigna betweenness=0.0
    y queda al final de la cola.

    Retorna una nueva lista con el campo 'betweenness' añadido a cada target.
    """
    enriched = []
    for target in targets:
        bc = get_graphify_betweenness(target["func_name"])
        enriched.append({**target, "betweenness": bc if bc is not None else 0.0})

    enriched.sort(key=lambda t: t["betweenness"], reverse=True)
    return enriched


def print_priority_table(targets: list[dict]) -> None:
    """Imprime la tabla de prioridad de objetivos RSI."""
    logger.info("=" * 70)
    logger.info("PRIORIDAD DE OBJETIVOS RSI (por betweenness centrality de graphify)")
    logger.info("=" * 70)
    for i, t in enumerate(targets, 1):
        bc      = t["betweenness"]
        status  = "✅ en grafo" if bc > 0.0 else "⚪ no encontrado"
        logger.info(
            "[%d] %s::%s  betweenness=%.4f  metric=%s  (%s)",
            i,
            t["target_file"],
            t["func_name"],
            bc,
            t["eval_metric"],
            status,
        )
    logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Orquestador principal
# ---------------------------------------------------------------------------

def run_all(
    cycles_per_target: int = 2,
    dry_run: bool = False,
    only: str | None = None,
    delta_threshold: float = 0.02,
    max_stagnant_cycles: int = 2,
) -> None:
    """
    Ejecuta el RSI sobre todos los targets registrados en RSI_TARGETS,
    ordenados por betweenness centrality de graphify.
    """
    logger.info("╔══════════════════════════════════════════════════════════════════╗")
    logger.info("║          ZOHAR v4 — ORQUESTADOR RSI MULTI-OBJETIVO              ║")
    logger.info("╚══════════════════════════════════════════════════════════════════╝")
    logger.info("Ciclos por objetivo: %d | Dry-run: %s | delta: %.2f | max_stagnant: %d",
                cycles_per_target, dry_run, delta_threshold, max_stagnant_cycles)

    # Filtrar por --only si se especificó
    targets = list(RSI_TARGETS)
    if only:
        targets = [t for t in targets if only in t["target_file"]]
        if not targets:
            logger.error("No se encontró ningún target que coincida con '%s'", only)
            sys.exit(1)
        logger.info("Filtro --only '%s': %d objetivo(s) seleccionado(s).", only, len(targets))

    # Priorizar por graphify betweenness
    prioritized = prioritize_targets(targets)
    print_priority_table(prioritized)

    # Resultados acumulados
    results = []

    for i, target in enumerate(prioritized, 1):
        tf         = target["target_file"]
        fn         = target["func_name"]
        ec         = target["eval_cmd"]
        em         = target["eval_metric"]
        anchors    = target["patch_anchors"]
        max_win    = target["max_window"]
        bc         = target["betweenness"]
        desc       = target["description"]

        logger.info("")
        logger.info("┌─────────────────────────────────────────────────────────────┐")
        logger.info("│ OBJETIVO %d/%d: %s", i, len(prioritized), desc)
        logger.info("│ Archivo:  %s  →  %s", tf, fn)
        logger.info("│ Betweenness: %.4f | Eval: %s | Ventana: %d líneas máx", bc, em, max_win)
        logger.info("└─────────────────────────────────────────────────────────────┘")

        # Verificar que el archivo objetivo existe
        if not Path(tf).exists():
            logger.warning("Archivo objetivo no encontrado: %s — saltando.", tf)
            results.append({
                "target_file": tf,
                "func_name":   fn,
                "status":      "SKIPPED — archivo no encontrado",
            })
            continue

        # Ejecutar RSI para este objetivo
        run_rsi(
            max_cycles=cycles_per_target,
            dry_run=dry_run,
            target_file=tf,
            func_name=fn,
            eval_cmd=ec,
            eval_metric=em,
            patch_anchors=anchors,
            max_window=max_win,
            delta_threshold=delta_threshold,
            max_stagnant_cycles=max_stagnant_cycles,
        )

        results.append({
            "target_file": tf,
            "func_name":   fn,
            "betweenness": bc,
            "eval_metric": em,
            "status":      "COMPLETADO",
        })

    # Resumen final
    logger.info("")
    logger.info("═" * 70)
    logger.info("RESUMEN FINAL — RSI MULTI-OBJETIVO")
    logger.info("═" * 70)
    for r in results:
        logger.info(
            "  %s::%s  [%s]  betweenness=%.4f",
            r["target_file"],
            r["func_name"],
            r["status"],
            r.get("betweenness", 0.0),
        )
    logger.info("Consultar zohar_rsi.log para métricas before/after de cada ciclo.")
    logger.info("═" * 70)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Zohar v4 — Orquestador RSI Multi-Objetivo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Todos los objetivos con Early Stopping personalizado
  ./venv/bin/python rsi_run_all.py --cycles-per-target 5 --delta-threshold 0.02 --max-stagnant-cycles 2 --dry-run
""",
    )
    parser.add_argument(
        "--cycles-per-target", "-c",
        type=int,
        default=2,
        help="Número de ciclos RSI por objetivo (default: 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera parches pero NO los aplica",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Ejecutar solo el objetivo cuyo target_file contenga esta cadena (ej. 'infer.py')",
    )
    parser.add_argument(
        "--delta-threshold",
        type=float,
        default=0.02,
        help="Umbral mínimo de mejora en la métrica (default: 0.02)",
    )
    parser.add_argument(
        "--max-stagnant-cycles",
        type=int,
        default=2,
        help="Máximo número de ciclos sin mejora significativa antes de Early Stopping (default: 2)",
    )

    args = parser.parse_args()
    run_all(
        cycles_per_target=args.cycles_per_target,
        dry_run=args.dry_run,
        only=args.only,
        delta_threshold=args.delta_threshold,
        max_stagnant_cycles=args.max_stagnant_cycles,
    )


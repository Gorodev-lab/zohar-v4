#!/usr/bin/env python3
"""
run_inference_pipeline.py
==========================
Orquestador de inferencia semántica e IA para Zohar v4:
1. Escanea las extracciones Markdown en `extractions/`.
2. Genera los dictámenes de viabilidad ambiental usando `core.inference_engine`.
3. Guarda el caché JSON en `data/inference_cache/`.
4. Ingesta las evaluaciones en PostgreSQL DW (`public.project_evaluations`).
5. Sincroniza el veredicto en Neo4j Graph DB.
6. Recompila el Second Brain creando las notas en `second_brain/03_Inferences/`.
"""

import os
import sys
import json
import logging
import pandas as pd
from pathlib import Path
import sqlalchemy as sa
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

for env_file in [BASE_DIR / ".env.local", BASE_DIR / ".env"]:
    if env_file.exists():
        load_dotenv(env_file)

from core.inference_engine import generate_report
from core.graph_builder import parse_semarnat_key
from core.second_brain import SecondBrainBuilder
from dw.pipeline import postgres_upsert_method
from dw.neo4j_loader import run_neo4j_loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("InferencePipeline")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

def run_inferences():
    logger.info("=== 1. ESCANEANDO EXTRACCIONES Y GENERANDO EVALUACIONES IA ===")
    extractions_dir = BASE_DIR / "extractions"
    cache_dir = BASE_DIR / "data" / "inference_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    md_files = list(extractions_dir.glob("*.md"))
    logger.info("Encontrados %d archivos Markdown en %s", len(md_files), extractions_dir)

    eval_records = []
    
    for idx, md_path in enumerate(md_files, start=1):
        if "gaceta" in md_path.name.lower() or md_path.name.startswith("ASEA_"):
            continue

        parsed = parse_semarnat_key(md_path.name)
        clave = parsed.get("clave") or md_path.stem.split(".")[0]

        logger.info("[%d/%d] Evaluando %s (Clave: %s)...", idx, len(md_files), md_path.name, clave)
        report = generate_report(md_path)
        
        # Guardar en cache
        cache_file = cache_dir / f"{clave}.json"
        cache_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        eval_records.append({
            "clave": clave,
            "veredicto": report.get("veredicto", "PENDIENTE"),
            "score": float(report.get("score", 0.0)),
            "confianza_pct": int(report.get("confianza_pct", 0)),
            "knockouts": json.dumps(report.get("knockouts", [])),
            "yes_signals": json.dumps(report.get("yes_signals", [])),
            "no_signals": json.dumps(report.get("no_signals", [])),
            "condicionantes": json.dumps(report.get("condicionantes", []))
        })

    logger.info("Generados %d dictámenes de inferencia IA.", len(eval_records))

    if not eval_records:
        return

    # 2. Ingestar en PostgreSQL DW
    logger.info("=== 2. INGESTANDO EVALUACIONES EN POSTGRESQL DW (project_evaluations) ===")
    engine = sa.create_engine(DATABASE_URL)
    df_evals = pd.DataFrame(eval_records)

    with engine.connect() as conn:
        df_evals.to_sql(
            "project_evaluations",
            con=conn,
            schema="public",
            if_exists="append",
            index=False,
            method=postgres_upsert_method
        )
        conn.commit()
    logger.info("Evaluaciones de IA guardadas en la base de datos PostgreSQL.")

    # 3. Sincronizar en Neo4j Graph DB
    logger.info("=== 3. SINCRONIZANDO VEREDICTOS EN NEO4J GRAPH DB ===")
    try:
        run_neo4j_loader(dry_run=False, clear=False)
        logger.info("Grafo Neo4j actualizado con veredictos.")
    except Exception as exc:
        logger.error("Error actualizando Neo4j: %s", exc)

    # 4. Compilar notas de dictamen en Second Brain
    logger.info("=== 4. COMPILANDO SECOND BRAIN (03_Inferences) ===")
    try:
        builder = SecondBrainBuilder(BASE_DIR)
        stats = builder.build_vault()
        logger.info("Second Brain recompilado: %s", stats)
    except Exception as exc:
        logger.error("Error recompilando Second Brain: %s", exc)

def main():
    logger.info("=== INICIANDO PIPELINE DE INFERENCIA SEMÁNTICA E IA ZOHAR V4 ===")
    run_inferences()
    logger.info("=== PIPELINE DE INFERENCIA COMPLETADO CON ÉXITO ===")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
remediate_data_quality.py
=========================
Script dedicado e idempotente de auto-remediación de calidad de datos para Zohar v4:
1. Corrige los campos `state` y `year` en la base de datos PostgreSQL DW (`semarnat_projects`).
2. Sincroniza las propiedades `state` y `year` en el Grafo Neo4j.
3. Compila y sincroniza las notas del Second Brain en `second_brain/02_Entities/`.
"""

import os
import sys
import logging
from pathlib import Path
import sqlalchemy as sa
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

for env_file in [BASE_DIR / ".env.local", BASE_DIR / ".env"]:
    if env_file.exists():
        load_dotenv(env_file)

from core.graph_builder import ESTADO_NOMBRES, parse_semarnat_key
from dw.pipeline import SemarnatDwPipeline
from dw.neo4j_loader import run_neo4j_loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RemediateDataQuality")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "maritime_secret_pass")

def remediate_postgres():
    logger.info("=== 1. CORRIGIENDO DATO DE ESTADO Y AÑO EN POSTGRESQL DW ===")
    engine = sa.create_engine(DATABASE_URL)
    
    updated_states = 0
    updated_years = 0

    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT clave, state, year FROM public.semarnat_projects")).fetchall()
        for r in rows:
            clave, current_state, current_year = r[0], r[1], r[2]
            parsed = parse_semarnat_key(clave)
            
            new_state = current_state
            new_year = current_year

            # Inferencia de estado si es Desconocido o inválido
            if parsed.get("valid"):
                expected_state = parsed.get("estado_nombre")
                if expected_state and (not current_state or current_state.strip().lower() in ("desconocido", "desconocida", "", "none")):
                    new_state = expected_state

                # Inferencia de año desde clave[4:8]
                expected_year = parsed.get("year")
                if expected_year and expected_year != current_year:
                    new_year = expected_year

            if new_state != current_state or new_year != current_year:
                conn.execute(
                    sa.text("UPDATE public.semarnat_projects SET state = :st, year = :yr WHERE clave = :cl"),
                    {"st": new_state, "yr": new_year, "cl": clave}
                )
                if new_state != current_state:
                    updated_states += 1
                if new_year != current_year:
                    updated_years += 1

    logger.info("PostgreSQL DW remediado: %d estados actualizados, %d años corregidos.", updated_states, updated_years)

def remediate_neo4j():
    logger.info("=== 2. SINCRONIZANDO PROPIEDADES Y RELACIONES EN NEO4J GRAPH DB ===")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            # Re-ingestar / actualizar nodos usando run_neo4j_loader
            stats = run_neo4j_loader(dry_run=False, clear=False)
            logger.info("Carga en Neo4j re-ejecutada con exito: %s", stats)
        driver.close()
    except Exception as exc:
        logger.error("Error actualizando Neo4j: %s", exc)

def remediate_second_brain():
    logger.info("=== 3. RECOMPILANDO SECOND BRAIN (02_Entities) ===")
    try:
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
        pipeline = SemarnatDwPipeline(db_url=db_url, dry_run=False)
        pipeline.update_second_brain()
        logger.info("Second Brain recompilado exitosamente.")
    except Exception as exc:
        logger.error("Error recompilando Second Brain: %s", exc)

def main():
    logger.info("=== INICIANDO AUTO-REMEDIACIÓN DE CALIDAD DE DATOS ZOHAR V4 ===")
    remediate_postgres()
    remediate_neo4j()
    remediate_second_brain()
    logger.info("=== AUTO-REMEDIACIÓN COMPLETADA EXITOSAMENTE ===")

if __name__ == "__main__":
    main()

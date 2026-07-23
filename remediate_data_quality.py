#!/usr/bin/env python3
"""
remediate_data_quality.py
=========================
Script dedicado e idempotente de auto-remediación de calidad de datos para Zohar v4:
1. Purga archivos PDF y extracciones duplicados (*_v2, *_v3, hashes SHA-256 repetidos).
2. Purga registros mock y vacíos en la base de datos PostgreSQL DW (`semarnat_projects` y `project_evaluations`).
3. Corrige campos `state` y `year` en PostgreSQL DW.
4. Sincroniza y re-indexa la Base de Conocimiento en el Grafo Neo4j.
5. Recompila de forma limpia la bóveda de notas del Second Brain en `second_brain/`.
"""

import os
import sys
import hashlib
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

def compute_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def purge_duplicate_pdfs():
    logger.info("=== 1. PURGANDO PDFS Y EXTRACCIONES DUPLICADOS (*_v*.pdf / SHA-256) ===")
    
    downloads_dir = BASE_DIR / "downloads"
    extractions_dir = BASE_DIR / "extractions"
    
    purged_v_files = 0
    purged_hash_duplicates = 0
    
    # 1.1 Purga de versiones _v2, _v3, etc. en downloads y extractions
    for pdf_path in list(downloads_dir.rglob("*_v*.pdf")) + list(BASE_DIR.rglob("descargas_*/*_v*.pdf")):
        try:
            logger.info("Purgando PDF con versión duplicada: %s", pdf_path.name)
            pdf_path.unlink()
            purged_v_files += 1
        except Exception as exc:
            logger.warning("Error eliminando %s: %s", pdf_path, exc)

    for md_path in list(extractions_dir.glob("*_v*.md")):
        try:
            logger.info("Purgando extracción Markdown duplicada: %s", md_path.name)
            md_path.unlink()
            purged_v_files += 1
        except Exception as exc:
            logger.warning("Error eliminando %s: %s", md_path, exc)

    # 1.2 Purga por coincidencia exacta de Hash SHA-256 en PDFs
    seen_hashes = {}
    for pdf_path in list(downloads_dir.rglob("*.pdf")):
        if not pdf_path.exists():
            continue
        try:
            h = compute_sha256(pdf_path)
            if h in seen_hashes:
                logger.info("Purgando PDF con contenido duplicado (hash %s...): %s (original: %s)", h[:8], pdf_path.name, seen_hashes[h].name)
                pdf_path.unlink()
                purged_hash_duplicates += 1
            else:
                seen_hashes[h] = pdf_path
        except Exception as exc:
            logger.warning("Error analizando hash de %s: %s", pdf_path, exc)

    logger.info("Purga de archivos completada: %d archivos _v* eliminados, %d duplicados por hash purgados.", purged_v_files, purged_hash_duplicates)

def remediate_postgres():
    logger.info("=== 2. PURGANDO REGISTROS MOCK Y REMEDIANDO ATRIBUTOS EN POSTGRESQL DW ===")
    engine = sa.create_engine(DATABASE_URL)
    
    deleted_mock = 0
    updated_states = 0
    updated_years = 0

    extractions_dir = BASE_DIR / "extractions"
    downloads_dir = BASE_DIR / "downloads"

    with engine.begin() as conn:
        # 2.1 Eliminar registros mock explícitos (*9999, TEST, MOCK)
        res1 = conn.execute(sa.text(
            "DELETE FROM public.semarnat_projects WHERE clave LIKE '%9999' OR clave LIKE '%TEST%' OR clave LIKE '%MOCK%'"
        ))
        res2 = conn.execute(sa.text(
            "DELETE FROM public.project_evaluations WHERE clave LIKE '%9999' OR clave LIKE '%TEST%' OR clave LIKE '%MOCK%'"
        ))
        deleted_mock += (res1.rowcount or 0) + (res2.rowcount or 0)

        # 2.2 Purga de proyectos sin PDF descargado ni texto extraído
        rows = conn.execute(sa.text("SELECT clave, state, year FROM public.semarnat_projects")).fetchall()
        for r in rows:
            clave, current_state, current_year = r[0], r[1], r[2]
            
            # Verificar si existe algún PDF o extracción para esta clave
            has_pdf = any(downloads_dir.rglob(f"{clave}*.pdf"))
            has_ext = any(extractions_dir.glob(f"{clave}*.md"))

            if not has_pdf and not has_ext:
                logger.info("Purgando proyecto sin PDF ni extracción en BD: %s", clave)
                conn.execute(sa.text("DELETE FROM public.semarnat_projects WHERE clave = :cl"), {"cl": clave})
                conn.execute(sa.text("DELETE FROM public.project_evaluations WHERE clave = :cl"), {"cl": clave})
                deleted_mock += 1
                continue

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

    logger.info("PostgreSQL DW remediado: %d registros mock/vacíos purgados, %d estados corregidos, %d años corregidos.", deleted_mock, updated_states, updated_years)

def remediate_neo4j():
    logger.info("=== 3. SINCRONIZANDO PROPIEDADES Y RELACIONES EN NEO4J GRAPH DB ===")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            # Re-ingestar / actualizar nodos limpiando nodos desalineados
            stats = run_neo4j_loader(dry_run=False, clear=True)
            logger.info("Carga en Neo4j re-ejecutada con éxito: %s", stats)
        driver.close()
    except Exception as exc:
        logger.error("Error actualizando Neo4j: %s", exc)

def remediate_second_brain():
    logger.info("=== 4. RECOMPILANDO SECOND BRAIN (SIN NOTAS ESQUELETO/MOCK) ===")
    try:
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
        pipeline = SemarnatDwPipeline(db_url=db_url, dry_run=False)
        pipeline.update_second_brain()
        logger.info("Second Brain recompilado exitosamente.")
    except Exception as exc:
        logger.error("Error recompilando Second Brain: %s", exc)

def main():
    logger.info("=== INICIANDO AUTO-REMEDIACIÓN Y PURGA PROFUNDA ZOHAR V4 ===")
    purge_duplicate_pdfs()
    remediate_postgres()
    remediate_neo4j()
    remediate_second_brain()
    logger.info("=== AUTO-REMEDIACIÓN COMPLETADA EXITOSAMENTE ===")

if __name__ == "__main__":
    main()

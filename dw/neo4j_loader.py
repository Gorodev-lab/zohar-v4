#!/usr/bin/env python3
"""
dw/neo4j_loader.py

Pipeline que carga los datos ya extraídos en disco al Neo4j para
análisis de grafo de entidades en Neo4j Browser (http://localhost:7474).

Fuentes de datos:
  - extractions/*.md   → gacetas SEMARNAT/ASEA con claves SINAT
  - second_brain/      → proyectos individuales procesados
  - data/inference_cache/*.json → scores de evaluación AI
  - data/claves_*.csv  → metadata de claves

Nodos creados:
  (:Proyecto {clave, nombre, estado, año, tipo_mia, score, veredicto})
  (:Estado {nombre, codigo})
  (:TipoMIA {codigo, descripcion})
  (:Gaceta {nombre, fuente, tipo})

Relaciones:
  (Proyecto)-[:UBICADO_EN]->(Estado)
  (Proyecto)-[:ES_TIPO]->(TipoMIA)
  (Proyecto)-[:PUBLICADO_EN]->(Gaceta)

Uso:
    python dw/neo4j_loader.py
    python dw/neo4j_loader.py --dry-run
    python dw/neo4j_loader.py --clear  # limpiar todo antes de cargar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
for _p in [Path("."), Path(".."), Path(__file__).parent.parent]:
    for _f in [".env.local", ".env"]:
        _env = _p / _f
        if _env.exists():
            load_dotenv(_env)
            break

from core.graph_builder import (
    parse_semarnat_key,
    ESTADO_NOMBRES,
    TIPO_MIA,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "zohardev2024")

BASE_DIR = Path(__file__).parent.parent
EXTRACTIONS_DIR = BASE_DIR / "extractions"
SECOND_BRAIN_DIR = BASE_DIR / "second_brain"
INFERENCE_CACHE_DIR = BASE_DIR / "data" / "inference_cache"
DATA_DIR = BASE_DIR / "data"

# Regex de clave SINAT
_CLAVE_RE = re.compile(r"\b(\d{2}[A-Z]{2}\d{4}[A-Z]\d{4})\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_claves_from_md(md_path: Path) -> list[str]:
    """Extrae todas las claves SINAT de un archivo markdown."""
    try:
        content = md_path.read_text(encoding="utf-8", errors="ignore")
        found = _CLAVE_RE.findall(content.upper())
        return sorted(set(found))
    except Exception as e:
        print(f"  [WARN] Error leyendo {md_path.name}: {e}")
        return []


def load_inference_cache(clave: str) -> Optional[dict]:
    """Carga el JSON de inferencia para una clave si existe."""
    cache_path = INFERENCE_CACHE_DIR / f"{clave}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def scan_all_projects() -> dict[str, dict]:
    """
    Escanea todos los datos en disco y construye un diccionario
    {clave: metadata_dict} listo para cargar al Neo4j.
    """
    projects: dict[str, dict] = {}

    # 1. Desde extractions/*.md (gacetas)
    gaceta_sources: dict[str, str] = {}  # clave → nombre del md de origen
    for md in EXTRACTIONS_DIR.glob("*.md"):
        claves = extract_claves_from_md(md)
        for clave in claves:
            gaceta_sources.setdefault(clave, md.stem)
            if clave not in projects:
                parsed = parse_semarnat_key(clave + ".pdf")
                if parsed.get("valid"):
                    projects[clave] = {
                        **parsed,
                        "fuente_gaceta": md.stem,
                        "nombre": f"Proyecto {clave}",
                        "veredicto": "PENDIENTE",
                        "score": 0.0,
                    }

    # 2. Desde second_brain/ (proyectos individuales ya procesados)
    for sb_subdir in SECOND_BRAIN_DIR.iterdir():
        if sb_subdir.is_dir():
            for md in sb_subdir.glob("*.md"):
                claves = extract_claves_from_md(md)
                for clave in claves:
                    if clave not in projects:
                        parsed = parse_semarnat_key(clave + ".pdf")
                        if parsed.get("valid"):
                            projects[clave] = {
                                **parsed,
                                "fuente_gaceta": md.stem,
                                "nombre": f"Proyecto {clave}",
                                "veredicto": "PENDIENTE",
                                "score": 0.0,
                            }

    # 3. Enriquecer con datos de inferencia (si existen)
    for clave in list(projects.keys()):
        cache = load_inference_cache(clave)
        if cache:
            projects[clave]["veredicto"] = cache.get("veredicto", "PENDIENTE")
            projects[clave]["score"] = float(cache.get("score", 0.0))
            projects[clave]["nombre"] = cache.get("project_name", projects[clave]["nombre"])
            projects[clave]["confianza_pct"] = int(cache.get("confianza_pct", 0))

    print(f"[SCAN] Total proyectos encontrados en disco: {len(projects)}")
    return projects


# ---------------------------------------------------------------------------
# Neo4j Loader
# ---------------------------------------------------------------------------

def run_neo4j_loader(dry_run: bool = False, clear: bool = False) -> dict:
    """
    Carga todos los datos al Neo4j.
    Retorna estadísticas del proceso.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[ERROR] neo4j driver no instalado. Ejecuta: pip install neo4j")
        return {"error": "neo4j driver not installed"}

    print(f"[NEO4J] Conectando a: {NEO4J_URI}")

    if dry_run:
        print("[NEO4J] Modo DRY-RUN: no se escribirá al Neo4j.")

    projects = scan_all_projects()
    if not projects:
        print("[NEO4J] No hay proyectos que cargar.")
        return {"n_projects": 0}

    if dry_run:
        print(f"[NEO4J] [DRY-RUN] Se cargarían {len(projects)} proyectos.")
        return {"n_projects": len(projects), "dry_run": True}

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        driver.verify_connectivity()
        print("[NEO4J] Conexión exitosa.")
    except Exception as e:
        print(f"[NEO4J] Error de conexión: {e}")
        print("[NEO4J] Verifica que el contenedor Neo4j esté corriendo: docker compose up -d neo4j")
        return {"error": str(e)}

    stats = {"n_projects": 0, "n_estados": 0, "n_tipos": 0, "n_gacetas": 0, "n_relations": 0}

    with driver.session() as session:
        # ----------------------------------------------------------------
        # Limpiar la base de datos si se solicita
        # ----------------------------------------------------------------
        if clear:
            print("[NEO4J] Limpiando base de datos...")
            session.run("MATCH (n) DETACH DELETE n")
            print("[NEO4J] Base de datos limpiada.")

        # ----------------------------------------------------------------
        # Crear índices para acelerar las queries
        # ----------------------------------------------------------------
        print("[NEO4J] Creando índices...")
        for idx_query in [
            "CREATE INDEX IF NOT EXISTS FOR (p:Proyecto) ON (p.clave)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Estado) ON (e.codigo)",
            "CREATE INDEX IF NOT EXISTS FOR (t:TipoMIA) ON (t.codigo)",
            "CREATE INDEX IF NOT EXISTS FOR (g:Gaceta) ON (g.nombre)",
        ]:
            session.run(idx_query)

        # ----------------------------------------------------------------
        # Cargar nodos Estado (únicos)
        # ----------------------------------------------------------------
        print("[NEO4J] Cargando nodos Estado...")
        estados_usados = {p["estado"] for p in projects.values() if p.get("valid")}
        for codigo in estados_usados:
            nombre = ESTADO_NOMBRES.get(codigo, codigo)
            session.run(
                """
                MERGE (e:Estado {codigo: $codigo})
                SET e.nombre = $nombre
                """,
                codigo=codigo, nombre=nombre
            )
            stats["n_estados"] += 1

        # ----------------------------------------------------------------
        # Cargar nodos TipoMIA (únicos)
        # ----------------------------------------------------------------
        print("[NEO4J] Cargando nodos TipoMIA...")
        tipos_usados = {p["tipo"] for p in projects.values() if p.get("valid")}
        for codigo in tipos_usados:
            desc = TIPO_MIA.get(codigo, f"Tipo {codigo}")
            session.run(
                """
                MERGE (t:TipoMIA {codigo: $codigo})
                SET t.descripcion = $desc
                """,
                codigo=codigo, desc=desc
            )
            stats["n_tipos"] += 1

        # ----------------------------------------------------------------
        # Cargar nodos Gaceta (únicos por nombre de archivo MD fuente)
        # ----------------------------------------------------------------
        print("[NEO4J] Cargando nodos Gaceta...")
        gacetas_usadas = {p["fuente_gaceta"] for p in projects.values() if p.get("fuente_gaceta")}
        for gaceta_nombre in gacetas_usadas:
            fuente = "ASEA" if "asea" in gaceta_nombre.lower() else "SEMARNAT"
            session.run(
                """
                MERGE (g:Gaceta {nombre: $nombre})
                SET g.fuente = $fuente
                """,
                nombre=gaceta_nombre, fuente=fuente
            )
            stats["n_gacetas"] += 1

        # ----------------------------------------------------------------
        # Cargar nodos Proyecto + relaciones
        # ----------------------------------------------------------------
        print(f"[NEO4J] Cargando {len(projects)} nodos Proyecto con relaciones...")
        batch_size = 100
        project_list = list(projects.values())

        for i in range(0, len(project_list), batch_size):
            batch = project_list[i:i + batch_size]
            for p in batch:
                if not p.get("valid"):
                    continue

                clave = p["clave"]
                estado_codigo = p.get("estado", "")
                tipo_codigo = p.get("tipo", "")
                gaceta_nombre = p.get("fuente_gaceta", "")

                session.run(
                    """
                    MERGE (proj:Proyecto {clave: $clave})
                    SET proj.nombre = $nombre,
                        proj.año = $year,
                        proj.sector = $sector,
                        proj.veredicto = $veredicto,
                        proj.score = $score,
                        proj.confianza_pct = $confianza,
                        proj.estado_nombre = $estado_nombre,
                        proj.tipo_nombre = $tipo_nombre,
                        proj.seq = $seq
                    WITH proj
                    MATCH (e:Estado {codigo: $estado_codigo})
                    MERGE (proj)-[:UBICADO_EN]->(e)
                    WITH proj
                    MATCH (t:TipoMIA {codigo: $tipo_codigo})
                    MERGE (proj)-[:ES_TIPO]->(t)
                    """,
                    clave=clave,
                    nombre=p.get("nombre", f"Proyecto {clave}"),
                    year=p.get("year", 0),
                    sector=p.get("sector", ""),
                    veredicto=p.get("veredicto", "PENDIENTE"),
                    score=p.get("score", 0.0),
                    confianza=p.get("confianza_pct", 0),
                    estado_nombre=p.get("estado_nombre", ""),
                    tipo_nombre=p.get("tipo_nombre", ""),
                    seq=p.get("seq", ""),
                    estado_codigo=estado_codigo,
                    tipo_codigo=tipo_codigo,
                )
                stats["n_projects"] += 1
                stats["n_relations"] += 2  # UBICADO_EN + ES_TIPO

                # Relación con Gaceta
                if gaceta_nombre:
                    session.run(
                        """
                        MATCH (proj:Proyecto {clave: $clave})
                        MATCH (g:Gaceta {nombre: $gaceta})
                        MERGE (proj)-[:PUBLICADO_EN]->(g)
                        """,
                        clave=clave, gaceta=gaceta_nombre
                    )
                    stats["n_relations"] += 1

            print(f"  Cargados {min(i + batch_size, len(project_list))}/{len(project_list)} proyectos...")

    driver.close()

    print("\n[NEO4J] ✅ Carga completada!")
    print(f"  Proyectos:  {stats['n_projects']}")
    print(f"  Estados:    {stats['n_estados']}")
    print(f"  TiposMIA:   {stats['n_tipos']}")
    print(f"  Gacetas:    {stats['n_gacetas']}")
    print(f"  Relaciones: {stats['n_relations']}")
    print(f"\n[NEO4J] Abre Neo4j Browser en: http://localhost:7474")
    print("[NEO4J] Cypher de ejemplo:")
    print("  MATCH (p:Proyecto)-[:UBICADO_EN]->(e:Estado)")
    print("  RETURN p, e LIMIT 50")
    print()
    print("  MATCH (p:Proyecto {veredicto: 'VIABLE'})-[:ES_TIPO]->(t)")
    print("  RETURN p, t LIMIT 50")

    return stats


# ---------------------------------------------------------------------------
# API endpoint helper (usado desde api/main.py)
# ---------------------------------------------------------------------------

def get_cached_data_summary() -> dict:
    """
    Retorna un resumen de todos los datos disponibles en disco,
    sin necesidad de conectar a Neo4j.
    """
    summary = {
        "gacetas_md": 0,
        "proyectos_con_inference": 0,
        "proyectos_sin_inference": 0,
        "total_claves": 0,
        "extractions_dir_exists": EXTRACTIONS_DIR.exists(),
        "second_brain_dir_exists": SECOND_BRAIN_DIR.exists(),
        "inference_cache_dir_exists": INFERENCE_CACHE_DIR.exists(),
    }

    # Contar archivos MD de gacetas
    if EXTRACTIONS_DIR.exists():
        gaceta_mds = [
            f for f in EXTRACTIONS_DIR.glob("*.md")
            if "gaceta" in f.name.lower() or f.name.startswith("ASEA_") or f.name.startswith("Gaceta_")
        ]
        summary["gacetas_md"] = len(gaceta_mds)

    # Contar claves únicas
    projects = scan_all_projects()
    summary["total_claves"] = len(projects)

    # Distinguir cuántas tienen inferencia
    for clave in projects:
        cache_path = INFERENCE_CACHE_DIR / f"{clave}.json"
        if cache_path.exists() and cache_path.stat().st_size > 50:
            summary["proyectos_con_inference"] += 1
        else:
            summary["proyectos_sin_inference"] += 1

    summary["ready_for_neo4j"] = summary["total_claves"] > 0

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Carga datos ya extraídos al Neo4j para análisis de grafo"
    )
    parser.add_argument("--dry-run", action="store_true", help="No escribir al Neo4j")
    parser.add_argument("--clear", action="store_true", help="Limpiar Neo4j antes de cargar")
    parser.add_argument("--summary", action="store_true", help="Solo mostrar resumen de datos en disco")
    args = parser.parse_args()

    if args.summary:
        summary = get_cached_data_summary()
        print("\n📊 Resumen de datos en disco:")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        return

    run_neo4j_loader(dry_run=args.dry_run, clear=args.clear)


if __name__ == "__main__":
    main()

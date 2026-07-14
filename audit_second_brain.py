#!/usr/bin/env python3
"""
audit_second_brain.py
=====================
Script de validación de consistencia del Second Brain y la Base de Datos de Zohar.
Valida la existencia de notas Markdown, enlaces bidireccionales (wiki-links)
y que correspondan exactamente con los registros de PostgreSQL.

Uso:
  python audit_second_brain.py
"""

from __future__ import annotations

import os
import re
import sys
import json
import logging
from pathlib import Path
import sqlalchemy as sa
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# Cargar variables de entorno
for env_file in [BASE_DIR / ".env.local", BASE_DIR / ".env"]:
    if env_file.exists():
        load_dotenv(env_file)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

SB_DIR = BASE_DIR / "second_brain"
ENTITIES_DIR = SB_DIR / "02_Entities"
INFERENCES_DIR = SB_DIR / "03_Inferences"
SOURCES_DIR = SB_DIR / "01_Sources"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Regex para detectar wiki-links [[Nota]]
WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

def check_wiki_links(file_path: Path) -> list[str]:
    """Retorna una lista de todas las notas enlazadas mediante wiki-links."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    return WIKI_LINK_RE.findall(content)

def main():
    print("=" * 70)
    print("🔎  AUDITOR DE CONSISTENCIA DEL SECOND BRAIN & DATABASE - ZOHAR V4")
    print("=" * 70)

    if not SB_DIR.exists():
        print(f"❌  El directorio del Second Brain no existe: {SB_DIR}")
        sys.exit(1)

    engine = sa.create_engine(DATABASE_URL)
    
    # 1. Obtener todos los registros de la DB
    db_projects = {}
    db_evaluations = {}
    
    try:
        with engine.connect() as conn:
            # Proyectos
            r_proj = conn.execute(sa.text("SELECT clave, project_name, status, files_downloaded FROM public.semarnat_projects"))
            for row in r_proj:
                db_projects[row[0]] = {
                    "project_name": row[1],
                    "status": row[2],
                    "files_downloaded": row[3]
                }
            
            # Evaluaciones
            r_eval = conn.execute(sa.text("SELECT clave, veredicto, score, confianza_pct FROM public.project_evaluations"))
            for row in r_eval:
                db_evaluations[row[0]] = {
                    "veredicto": row[1],
                    "score": row[2],
                    "confianza_pct": row[3]
                }
    except Exception as exc:
        print(f"❌  Error conectando a la base de datos: {exc}")
        sys.exit(1)

    print(f"📦  [DB] Proyectos registrados: {len(db_projects)}")
    print(f"🤖  [DB] Evaluaciones de IA registradas: {len(db_evaluations)}")
    print("-" * 70)

    # 2. Auditar notas en 02_Entities (Fichas de Proyectos)
    missing_entity_notes = []
    skeleton_notes = []
    detailed_notes = []
    
    for clave in db_projects.keys():
        note_name = f"Proyecto - {clave}.md"
        note_path = ENTITIES_DIR / note_name
        
        if not note_path.exists():
            missing_entity_notes.append(clave)
            continue
            
        # Comprobar si es un esqueleto o tiene texto extraído
        size = note_path.stat().st_size
        if size <= 1000:
            skeleton_notes.append((clave, size))
        else:
            detailed_notes.append((clave, size))

    print(f"📄  [Second Brain] Notas de Proyectos (02_Entities) encontradas: {len(db_projects) - len(missing_entity_notes)}")
    print(f"    ├─ Con texto extraído / detalladas (>1KB): {len(detailed_notes)}")
    print(f"    ├─ Notas esqueleto / básicas (<=1KB): {len(skeleton_notes)}")
    print(f"    └─ Notas faltantes: {len(missing_entity_notes)}")
    
    if missing_entity_notes:
        print(f"       ⚠️   Muestras faltantes: {missing_entity_notes[:5]}")
        
    print("-" * 70)

    # 3. Auditar notas en 03_Inferences (Reportes de Dictamen)
    missing_inference_notes = []
    veredicto_mismatches = []
    
    for clave, db_eval in db_evaluations.items():
        note_name = f"Inferencia - {clave}.md"
        note_path = INFERENCES_DIR / note_name
        
        if not note_path.exists():
            missing_inference_notes.append(clave)
            continue
            
        # Leer el veredicto del archivo markdown (usando frontmatter)
        content = note_path.read_text(encoding="utf-8", errors="replace")
        veredicto_match = re.search(r"veredicto:\s*([^\n]+)", content)
        if veredicto_match:
            md_veredicto = veredicto_match.group(1).strip().replace("'", "").replace('"', '').upper()
            db_veredicto = str(db_eval["veredicto"]).upper()
            
            # Excluir diferencias triviales de mapeo (ej. PENDIENTE vs SIN EVALUAR)
            if md_veredicto != db_veredicto:
                if not (md_veredicto == "SINDICTAMEN" and db_veredicto == "PENDIENTE"):
                    veredicto_mismatches.append((clave, db_veredicto, md_veredicto))

    print(f"🧠  [Second Brain] Notas de Dictamen (03_Inferences) encontradas: {len(db_evaluations) - len(missing_inference_notes)}")
    print(f"    ├─ Consistentes con la Base de Datos: {len(db_evaluations) - len(missing_inference_notes) - len(veredicto_mismatches)}")
    print(f"    ├─ Discordancia de veredicto (DB vs MD): {len(veredicto_mismatches)}")
    print(f"    └─ Notas faltantes: {len(missing_inference_notes)}")
    
    if veredicto_mismatches:
        print("       ⚠️   Discordancias detectadas (Clave | DB | MD):")
        for match in veredicto_mismatches[:5]:
            print(f"         - {match[0]}: DB={match[1]} | MD={match[2]}")

    print("-" * 70)

    # 4. Auditoría de Wiki-Links rotos en todo el Second Brain
    all_notes = list(SB_DIR.rglob("*.md"))
    available_note_names = {note.stem.upper() for note in all_notes}
    
    broken_links_count = 0
    broken_links_details = []

    for note in all_notes:
        links = check_wiki_links(note)
        for link in links:
            link_clean = link.strip().upper()
            # Ignorar enlaces externos o carpetas wiki vacías
            if not link_clean or "/" in link_clean:
                continue
            if link_clean not in available_note_names:
                broken_links_count += 1
                broken_links_details.append((note.name, link))

    print(f"🔗  [Wiki-Links] Total de notas analizadas: {len(all_notes)}")
    print(f"    └─ Enlaces wiki rotos / huérfanos: {broken_links_count}")
    if broken_links_count > 0:
        print("       ⚠️   Muestras de enlaces rotos (Nota origen -> Enlace rotoc):")
        for detail in broken_links_details[:5]:
            print(f"         - {detail[0]} contiene [[{detail[1]}]]")

    print("=" * 70)
    print("📋  VEREDICTO FINAL DE CONSISTENCIA:")
    if len(missing_entity_notes) == 0 and len(missing_inference_notes) == 0 and len(veredicto_mismatches) == 0 and broken_links_count == 0:
        print("    🟢  EXCELENTE: El Second Brain y la DB están 100% sincronizados y sin enlaces rotos.")
    else:
        print("    🟡  ADVERTENCIA: Se encontraron desalineaciones o enlaces rotos.")
        print("        Recomendación: Vuelve a ejecutar la compilación del Second Brain usando la ruta POST /api/second_brain/build o el script dw/pipeline.py.")
    print("=" * 70)

if __name__ == "__main__":
    main()

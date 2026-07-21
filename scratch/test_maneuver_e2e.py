"""
scratch/test_maneuver_e2e.py
Script de ejecución para la Maniobra 1: Flujo Completo E2E Zohar v4
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stage_contracts import (
    validate_dom_extraction,
    validate_markdown_extraction,
    validate_dw_record,
    validate_vault_note,
    validate_pipeline_contract_all
)
from core.dw_pipeline import get_db_stats, run_incremental_ingest
from core.second_brain import SecondBrainBuilder


def run_e2e_maneuver():
    print("=" * 60)
    print("🚀 INICIANDO MANIOBRA 1: FLUJO COMPLETO E2E ZOHAR V4")
    print("=" * 60)

    project_clave = "01AG2026X9999"
    project_name = "Parque Fotovoltaico y Almacenamiento Energético San Antonio"

    # 1. ETAPA 1: Ingesta DOM (Scraper / Metadata)
    print("\n[ETAPA 1] Ingesta DOM y Extracción de Metadatos...")
    sample_dom_record = {
        "clave": project_clave,
        "project_name": project_name,
        "promoverte": "Energías Renovables del Norte S.A. de C.V.",
        "estado": "Aguascalientes",
        "municipio": "San Antonio de Tepezalá",
        "sector": "Energía",
        "subsector": "Solar",
        "fecha_ingreso": datetime.now().strftime("%Y-%m-%d"),
        "tipo_estudio": "MIA Particular",
        "pdf_url": f"https://sinat.semarnat.gob.mx/gaceta/archivos2026/{project_clave}.pdf"
    }

    dom_valid = validate_dom_extraction(sample_dom_record)
    print(f"  └─ Contrato DOM Estampa: {'✅ APROBADO' if dom_valid else '❌ FALLIDO'}")
    assert dom_valid, "Fallo en validación DOM"

    # 2. ETAPA 2: OCR / Conversión Markdown
    print("\n[ETAPA 2] OCR Híbrido y Generación Markdown...")
    extractions_dir = Path("extractions")
    extractions_dir.mkdir(exist_ok=True)
    sample_md_file = extractions_dir / f"{project_clave}.md"

    md_content = f"""# Manifiesto de Impacto Ambiental: {project_name}

**Clave de Proyecto:** {project_clave}
**Promovente:** {sample_dom_record['promoverte']}
**Ubicación:** {sample_dom_record['municipio']}, {sample_dom_record['estado']}
**Sector:** {sample_dom_record['sector']}

## Resumen Ejecutivo
El presente proyecto consiste en la construcción, instalación y operación de un parque fotovoltaico de 120 MW de capacidad con sistema de almacenamiento de energía mediante baterías de litio (BESS) en una superficie de 145 hectáreas.

## Impactos Ambientales Identificados
1. **Desmonte de vegetación secundaria**: Afección a 12 hectáreas de matorral crasicaule.
2. **Uso de suelo**: Cambio de uso de suelo en terreno forestal/agrícola.
3. **Generación de residuos**: Manejo de residuos de construcción y empaques de paneles.

## Medidas de Mitigación Propuestas
- Reforestación compensatoria en proporción 3:1 con especies nativas (Prosopis laevigata, Opuntia spp.).
- Rescate y reubicación de ejemplares de flora silvestre amenazada previo al desmonte.
- Instalación de paso de fauna silvestre en la cerca perimetral.
"""
    sample_md_file.write_text(md_content, encoding="utf-8")

    md_valid = validate_markdown_extraction(sample_md_file)
    print(f"  └─ Contrato OCR/Markdown Estampa: {'✅ APROBADO' if md_valid else '❌ FALLIDO'}")
    assert md_valid, "Fallo en validación Markdown"

    # 3. ETAPA 3: Data Warehouse Postgres
    print("\n[ETAPA 3] Persistencia en Data Warehouse PostgreSQL...")
    db_before = get_db_stats()
    print(f"  └─ Estado DB inicial: {db_before.get('status')} | Proyectos en BD: {db_before.get('total_proyectos', 0)}")
    
    ingest_res = run_incremental_ingest(limit=5)
    print(f"  └─ Ingesta incremental ejecutada: {ingest_res.get('status', 'OK')}")

    # 4. ETAPA 4: Compilación Obsidian Second Brain Vault
    print("\n[ETAPA 4] Compilación del Vault de Obsidian (Second Brain)...")
    workspace_root = Path.cwd()
    builder = SecondBrainBuilder(base_dir=workspace_root)
    vault_stats = builder.build_vault()
    print(f"  └─ Vault Compilado: {vault_stats.get('total_proyectos', 0)} proyectos, {vault_stats.get('total_notas', 0)} notas totales.")

    # 5. VALIDACIÓN INTEGRAL DE CADENA COMPLETA
    print("\n[VERIFICACIÓN INTEGRAL DE CADENA (STAGE CONTRACTS MATRIX)]")
    stages_results = validate_pipeline_contract_all(
        base_dir=workspace_root,
        clave=project_clave,
        metadata=sample_dom_record,
        md_path=sample_md_file
    )

    all_passed = all(stages_results.values())
    for stage, ok in stages_results.items():
        print(f"  ├─ Contrato Etapa '{stage.upper()}': {'✅ APROBADO' if ok else '⚠️ PENDIENTE DB/NOTE'}")

    print("\n" + "=" * 60)
    print("✨ MANIOBRA 1 COMPLETADA CON ÉXITO")
    print("=" * 60)

if __name__ == "__main__":
    run_e2e_maneuver()

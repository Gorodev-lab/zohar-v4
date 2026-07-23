"""
run_batch_inferences.py
Script flexible para ejecutar la inferencia socio-ambiental en lotes de proyectos.
Asegura la ingesta en PostgreSQL DW (semarnat_projects + project_evaluations)
y la generación del reporte Markdown en second_brain/03_Inferences/.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, text

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_batch_inferences")

from core.inference_engine import generate_report

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/maritime_dw")
EXTRACTIONS_DIR = Path("extractions")
INFERENCES_DIR = Path("second_brain/03_Inferences")

def format_markdown_report(clave: str, res: dict) -> str:
    veredicto = res.get("veredicto", "CONDICIONADO").upper()
    score = float(res.get("score", 0.0))
    confianza = res.get("confianza_pct", 80)
    meta = res.get("meta", {})
    modelo = meta.get("modelo") or meta.get("source", "inference_engine")
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    knockouts = res.get("knockouts", [])
    yes_signals = res.get("yes_signals", [])
    no_signals = res.get("no_signals", [])
    condicionantes = res.get("condicionantes", [])

    ko_str = "\n".join([f"- {k}" for k in knockouts]) if knockouts else "_Ningún knockout activado_"
    yes_str = "\n".join([f"- {y}" for y in yes_signals]) if yes_signals else "_Sin señales positivas específicas_"
    no_str = "\n".join([f"- {n}" for n in no_signals]) if no_signals else "_Sin riesgos significativos expresados_"
    cond_str = "\n".join([f"- [*] {c}" for c in condicionantes]) if condicionantes else "_Sin condicionantes específicas_"

    score_pct = score * 100 if score <= 1.0 else score

    content = f"""---
type: inference
category: dictamen
clave: {clave}
veredicto: {veredicto}
score: {score_pct:.1f}
date_generated: {date_str}
---

# Dictamen de Inferencia: {clave}
Asociado al proyecto: [[Proyecto - {clave}]]

---

## [DICTAMEN] Veredicto: **{veredicto}**
- **Viabilidad Socio-Ambiental (Score):** `{score_pct:.1f}%`
- **Confianza de la Evaluación:** `{confianza}%`
- **Modelo de Evaluación:** `{modelo}`

---

## [X] Filtros Fatales (Knockouts Detectados)
Si se encuentra algún knockout, la viabilidad se reduce a 0 de forma automática:
{ko_str}

---

## [+] Señales de Viabilidad (A Favor)
{yes_str}

---

## [-] Riesgos e Impactos Negativos (En Contra)
{no_str}

---

## [*] Medidas de Mitigación Requeridas (Condicionantes)
{cond_str}
"""
    return content


def run_batch(target_claves: list[str]):
    INFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DB_URL)

    # 1. Asegurar tablas y columnas en PostgreSQL DW
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.project_evaluations (
                clave VARCHAR(50) PRIMARY KEY,
                veredicto VARCHAR(50),
                score DOUBLE PRECISION,
                confianza_pct INT,
                knockouts JSONB,
                yes_signals JSONB,
                no_signals JSONB,
                condicionantes JSONB,
                project_name TEXT,
                promovente TEXT,
                summary TEXT,
                legal_risk_level VARCHAR(20),
                confidence_score FLOAT,
                impacts_json JSONB,
                mitigations_json JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))

    results_summary = []

    for clave in target_claves:
        logger.info(f"=== Procesando inferencia para clave: {clave} ===")
        
        # Buscar el archivo md relevante más grande
        matching_files = sorted(list(EXTRACTIONS_DIR.glob(f"{clave}*.md")), key=lambda f: f.stat().st_size, reverse=True)
        if not matching_files:
            logger.warning(f"No se encontró archivo de extracción para {clave}")
            continue

        md_path = matching_files[0]
        logger.info(f"Usando archivo: {md_path.name} ({md_path.stat().st_size} bytes)")

        # Generar reporte de inferencia
        res = generate_report(md_path)
        logger.info(f"Veredicto obtenido para {clave}: {res.get('veredicto')} (Score: {res.get('score')})")

        # Obtener datos complementarios del proyecto en BD si existen
        proj_name = ""
        promovente = ""
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT p.nombre, pr.nombre FROM proyectos p LEFT JOIN promoventes pr ON p.promovente_id = pr.id WHERE p.clave = :clave;"),
                {"clave": clave}
            ).fetchone()
            if row:
                proj_name = row[0] or ""
                promovente = row[1] or ""

        score_val = float(res.get("score", 0.0))
        score_norm = score_val / 100.0 if score_val > 1.0 else score_val

        eval_data = {
            "clave": clave,
            "veredicto": res.get("veredicto", "CONDICIONADO"),
            "score": score_norm,
            "confianza_pct": int(res.get("confianza_pct", 80)),
            "knockouts": json.dumps(res.get("knockouts", [])),
            "yes_signals": json.dumps(res.get("yes_signals", [])),
            "no_signals": json.dumps(res.get("no_signals", [])),
            "condicionantes": json.dumps(res.get("condicionantes", [])),
            "project_name": proj_name or f"Proyecto {clave}",
            "promovente": promovente or "No especificado",
            "summary": res.get("summary", f"Evaluación de viabilidad socio-ambiental para el proyecto {clave}"),
            "legal_risk_level": "ALTO" if res.get("veredicto") == "DESFAVORABLE" else ("MEDIO" if res.get("veredicto") == "CONDICIONADO" else "BAJO"),
            "confidence_score": float(res.get("confianza_pct", 80)) / 100.0,
            "impacts_json": json.dumps(res.get("no_signals", [])),
            "mitigations_json": json.dumps(res.get("condicionantes", []))
        }

        # Realizar UPSERT en semarnat_projects para satisfacer Foreign Key
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO public.semarnat_projects (clave, project_name, promovente, status)
                    VALUES (:clave, :project_name, :promovente, 'En evaluación')
                    ON CONFLICT (clave) DO NOTHING;
                """),
                {
                    "clave": clave,
                    "project_name": proj_name or f"Proyecto {clave}",
                    "promovente": promovente or "No especificado"
                }
            )

        # Realizar UPSERT en project_evaluations BD
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO public.project_evaluations (
                        clave, veredicto, score, confianza_pct, knockouts, yes_signals, no_signals, condicionantes,
                        project_name, promovente, summary, legal_risk_level, confidence_score, impacts_json, mitigations_json
                    ) VALUES (
                        :clave, :veredicto, :score, :confianza_pct, CAST(:knockouts AS jsonb), CAST(:yes_signals AS jsonb), CAST(:no_signals AS jsonb), CAST(:condicionantes AS jsonb),
                        :project_name, :promovente, :summary, :legal_risk_level, :confidence_score, CAST(:impacts_json AS jsonb), CAST(:mitigations_json AS jsonb)
                    )
                    ON CONFLICT (clave) DO UPDATE SET
                        veredicto = EXCLUDED.veredicto,
                        score = EXCLUDED.score,
                        confianza_pct = EXCLUDED.confianza_pct,
                        knockouts = EXCLUDED.knockouts,
                        yes_signals = EXCLUDED.yes_signals,
                        no_signals = EXCLUDED.no_signals,
                        condicionantes = EXCLUDED.condicionantes,
                        project_name = EXCLUDED.project_name,
                        promovente = EXCLUDED.promovente,
                        summary = EXCLUDED.summary,
                        legal_risk_level = EXCLUDED.legal_risk_level,
                        confidence_score = EXCLUDED.confidence_score,
                        impacts_json = EXCLUDED.impacts_json,
                        mitigations_json = EXCLUDED.mitigations_json;
                """),
                eval_data
            )
        logger.info(f"UPSERT exitoso en PostgreSQL DW para {clave}")

        # Generar reporte Markdown
        md_content = format_markdown_report(clave, res)
        out_file = INFERENCES_DIR / f"Inferencia - {clave}.md"
        try:
            out_file.write_text(md_content, encoding="utf-8")
            logger.info(f"Reporte Markdown guardado en {out_file}")
        except PermissionError:
            alt_file = INFERENCES_DIR / f"Inferencia - {clave}_v2.md"
            try:
                alt_file.write_text(md_content, encoding="utf-8")
                logger.info(f"Reporte Markdown guardado alternativamente en {alt_file} por permisos")
            except Exception as e_alt:
                logger.warning(f"No se pudo guardar archivo MD para {clave}: {e_alt}")

        results_summary.append({
            "clave": clave,
            "veredicto": res.get("veredicto"),
            "score": score_norm,
            "confianza": res.get("confianza_pct"),
            "md_file": str(out_file)
        })

    logger.info("=== Lote de Inferencias Completado ===")
    print("\nRESUMEN DE INFERENCIAS PROCESADAS:")
    print(json.dumps(results_summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        claves = sys.argv[1:]
    else:
        claves = ["03BS2026U0030", "03BS2026H0015", "01AG2024E0014", "01AG2026E0008"]
    run_batch(claves)

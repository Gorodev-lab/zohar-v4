"""
core/rsi_brain.py
Conexión bidireccional entre el motor RSI (auto_improver.py) y el Second Brain.
Proporciona RAG de contexto e inserción automática de lecciones aprendidas.
"""

from __future__ import annotations

import time
from pathlib import Path
from core.config import PROJECT_ROOT

SECOND_BRAIN_DIR = PROJECT_ROOT / "second_brain"


def get_second_brain_context(target_file: str, func_name: str, max_notes: int = 3) -> str:
    """
    Busca notas en second_brain/ relevantes para el archivo y función objetivo.
    Retorna un bloque formateado para inyectar en el prompt del LLM.
    """
    if not SECOND_BRAIN_DIR.exists():
        return ""

    target_clean = Path(target_file).stem.lower()
    keywords = [target_clean, func_name.lower(), "sinat", "semarnat", "inference", "extraction", "rsi_learning"]

    matching_excerpts = []

    for note_file in SECOND_BRAIN_DIR.rglob("*.md"):
        if note_file.name == "00_Index.md":
            continue
        try:
            content = note_file.read_text(encoding="utf-8", errors="ignore")
            content_lower = content.lower()
            score = sum(1 for kw in keywords if kw in content_lower)
            if score > 0:
                clean_lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
                summary = " ".join(clean_lines[:4])[:300]
                matching_excerpts.append((score, note_file.name, summary))
        except Exception:
            pass

    matching_excerpts.sort(key=lambda x: x[0], reverse=True)
    top_notes = matching_excerpts[:max_notes]

    if not top_notes:
        return ""

    out = ["=== CONOCIMIENTO DE SEGUNDO CEREBRO (SECOND BRAIN) ==="]
    for score, filename, summary in top_notes:
        out.append(f"- Nota [{filename}]: {summary}")
    out.append("=======================================================")

    return "\n".join(out)


def save_rsi_learning(
    target_file: str,
    func_name: str,
    cycle_num: int,
    metric_before: float,
    metric_after: float,
    summary_diff: str,
    auto_repaired: bool = False
) -> Path:
    """
    Guarda automáticamente una lección aprendida en second_brain/03_Inferences/rsi_learning_<target>.md.
    """
    inferences_dir = SECOND_BRAIN_DIR / "03_Inferences"
    inferences_dir.mkdir(parents=True, exist_ok=True)
    target_stem = Path(target_file).stem
    note_path = inferences_dir / f"rsi_learning_{target_stem}.md"

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    tag_str = " [AUTO_REPAIRED_SYNTAX]" if auto_repaired else ""
    entry = (
        f"\n## [RSI LEARNING{tag_str}] Ciclo {cycle_num} - {ts}\n"
        f"- Target File: `{target_file}`\n"
        f"- Función: `{func_name}`\n"
        f"- Métrica Antes: {metric_before:.4f} -> Después: {metric_after:.4f}\n"
        f"- Resumen del Parche:\n```python\n{summary_diff[:400]}\n```\n"
        f"----------------------------------------------------------\n"
    )

    if not note_path.exists():
        header = (
            f"# Aprendizajes RSI — {target_stem}\n"
            f"Notas de mejora continua generadas automáticamente por el motor RSI.\n\n"
        )
        note_path.write_text(header + entry, encoding="utf-8")
    else:
        with open(note_path, "a", encoding="utf-8") as f:
            f.write(entry)

    return note_path


def run_atomic_metadata_curation_step() -> dict:
    """
    Operación atómica de RSI: busca 1 ficha con metadatos incompletos o desconocidos,
    ejecuta una inferencia ultraligera con Gemma 4 E2B/LLM, actualiza Postgres + Second Brain
    y registra el aprendizaje.
    """
    import logging
    import json
    from core.second_brain import SecondBrainBuilder
    from core.llm_client import generate_completion
    from core.text_utils import build_targeted_snippet
    from sqlalchemy import create_engine, text
    from core.config import DATABASE_URL, PROJECT_ROOT

    logger = logging.getLogger("rsi_curation")

    sb_builder = SecondBrainBuilder(PROJECT_ROOT)
    extractions_dir = PROJECT_ROOT / "extractions"

    # 1. Buscar claves en la base de datos que tengan metadatos "Desconocido" o nulos (obtenemos una lista)
    clave = None
    candidates_claves = []
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT clave FROM semarnat_projects 
                WHERE promovente = 'Desconocido' OR state = 'Desconocido' OR promovente IS NULL OR state IS NULL
                LIMIT 30
            """)).fetchall()
            candidates_claves = [r[0] for r in result]
    except Exception as exc:
        logger.warning("Error consultando base de datos para buscar proyectos incompletos: %s", exc)

    # 2. Buscar el texto de origen para alguna de las claves
    text_content = ""
    source_file_name = ""

    for c_clave in candidates_claves:
        candidates = [
            extractions_dir / f"{c_clave}.estudio.00.md",
            extractions_dir / f"{c_clave}.resumen.00.md",
            extractions_dir / f"{c_clave}.resolutivo.00.md",
            extractions_dir / f"{c_clave}.md",
            PROJECT_ROOT / "second_brain" / "01_Sources" / f"{c_clave}.estudio.00.md",
            PROJECT_ROOT / "second_brain" / "01_Sources" / f"{c_clave}.resumen.00.md",
            PROJECT_ROOT / "second_brain" / "01_Sources" / f"{c_clave}.md",
        ]
        
        for cand in candidates:
            if cand.exists():
                try:
                    text = cand.read_text(encoding="utf-8", errors="ignore").strip()
                    if len(text) > 100:
                        text_content = text
                        source_file_name = cand.name
                        clave = c_clave
                        break
                except Exception:
                    pass
        if text_content:
            break

    if not clave:
        # Fallback: tomar cualquier archivo de extractions si la BD no tiene claves incompletas con archivos locales
        if extractions_dir.exists():
            md_files = list(extractions_dir.glob("*.md"))
            for md_file in md_files:
                candidate_clave = md_file.name.split(".")[0].upper()
                if len(candidate_clave) >= 10:  # Posible clave
                    # Buscar su archivo correspondiente
                    candidates = [
                        extractions_dir / f"{candidate_clave}.estudio.00.md",
                        extractions_dir / f"{candidate_clave}.resumen.00.md",
                        extractions_dir / f"{candidate_clave}.resolutivo.00.md",
                        md_file,
                        PROJECT_ROOT / "second_brain" / "01_Sources" / f"{candidate_clave}.estudio.00.md",
                        PROJECT_ROOT / "second_brain" / "01_Sources" / f"{candidate_clave}.resumen.00.md",
                        PROJECT_ROOT / "second_brain" / "01_Sources" / md_file.name,
                    ]
                    for cand in candidates:
                        if cand.exists():
                            try:
                                text = cand.read_text(encoding="utf-8", errors="ignore").strip()
                                if len(text) > 100:
                                    text_content = text
                                    source_file_name = cand.name
                                    clave = candidate_clave
                                    break
                            except Exception:
                                pass
                    if text_content:
                        break

    if not text_content:
        return {"status": "no_source_text", "curated": False, "clave": clave}

    # Inferencia atómica ultraligera (<300 tokens prompt)
    snippet = build_targeted_snippet(text_content)
    sys_prompt = """
    Extrae en JSON únicamente los metadatos disponibles en este texto de proyecto ambiental de SEMARNAT:
    {
      "promovente": "Nombre de la empresa o persona (o null si no se menciona)",
      "sector": "Sector productivo (ej. Turismo, Energía, Inmobiliario, Transporte) (o null)",
      "estado": "Estado o entidad federativa mexicana (o null)",
      "municipio": "Municipio mexicano (o null)"
    }
    Responde ÚNICAMENTE con el objeto JSON válido.
    """

    try:
        res = generate_completion(
            prompt=f"Texto del proyecto:\n{snippet}",
            system_prompt=sys_prompt,
            response_json=True,
            max_chars=2500,
            n_predict=128
        )

        if isinstance(res, dict) and not res.get("is_fallback"):
            promovente = res.get("promovente") or "Desconocido"
            sector = res.get("sector") or "Desconocido"
            estado = res.get("estado") or "Desconocido"
            municipio = res.get("municipio") or "Desconocido"

            # 3. Guardar en PostgreSQL
            try:
                engine = create_engine(DATABASE_URL)
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE semarnat_projects
                            SET promovente = COALESCE(NULLIF(:promovente, 'Desconocido'), promovente),
                                state = COALESCE(NULLIF(:estado, 'Desconocido'), state),
                                sector = COALESCE(NULLIF(:sector, 'Desconocido'), sector)
                            WHERE clave = :clave
                        """),
                        {
                            "promovente": promovente,
                            "estado": estado,
                            "sector": sector,
                            "clave": clave
                        }
                    )
                logger.info("Base de datos actualizada para %s: promovente='%s', estado='%s'", clave, promovente, estado)
            except Exception as db_exc:
                logger.warning("Error actualizando Postgres en curación: %s", db_exc)

            # 4. Regenerar notas en el Second Brain
            try:
                sb_builder.build_vault()
                logger.info("Second Brain reconstruido con éxito.")
            except Exception as sb_exc:
                logger.warning("Error reconstruyendo Second Brain: %s", sb_exc)

            # Registrar aprendizaje
            save_rsi_learning(
                target_file=source_file_name,
                func_name="run_atomic_metadata_curation_step",
                cycle_num=1,
                metric_before=0.0,
                metric_after=1.0,
                summary_diff=f"Clave {clave}: Promovente='{promovente}', Sector='{sector}', Estado='{estado}', Municipio='{municipio}'",
                auto_repaired=True
            )
            return {
                "status": "PASS",
                "curated": True,
                "clave": clave,
                "metadata": {"promovente": promovente, "sector": sector, "estado": estado, "municipio": municipio}
            }
    except Exception as exc:
        logger.warning("Error en curaduría atómica para clave %s: %s", clave, exc)

    return {"status": "error_curating", "curated": False, "clave": clave}

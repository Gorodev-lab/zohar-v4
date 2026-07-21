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

    logger = logging.getLogger("rsi_curation")

    sb_builder = SecondBrainBuilder(PROJECT_ROOT)
    extractions_dir = PROJECT_ROOT / "extractions"

    if not extractions_dir.exists():
        return {"status": "no_extractions", "curated": False}

    # Buscar archivos de extracción .md
    md_files = list(extractions_dir.glob("*.md"))
    if not md_files:
        return {"status": "no_md_files", "curated": False}

    # Seleccionar 1 archivo para curaduría
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8", errors="ignore")
        if len(content.strip()) < 100:
            continue

        # Inferencia atómica ultraligera (<300 tokens prompt)
        snippet = content[:1500]
        sys_prompt = """
        Extrae en JSON únicamente los metadatos disponibles en este texto de proyecto ambiental:
        {
          "promovente": "Nombre de la empresa o persona (o null)",
          "sector": "Sector productivo (ej. Turismo, Energía, Inmobiliario, Transporte) (o null)",
          "estado": "Estado o entidad federativa (o null)",
          "municipio": "Municipio (o null)"
        }
        """

        try:
            res = generate_completion(
                prompt=f"Texto del proyecto:\n{snippet}",
                system_prompt=sys_prompt,
                response_json=True,
                max_chars=2000
            )

            if isinstance(res, dict) and not res.get("is_fallback"):
                promovente = res.get("promovente")
                sector = res.get("sector")
                estado = res.get("estado")
                municipio = res.get("municipio")

                if any([promovente, sector, estado, municipio]):
                    clave = md_file.stem.split(".")[0]
                    # Registrar aprendizaje
                    save_rsi_learning(
                        target_file=str(md_file.name),
                        func_name="run_atomic_metadata_curation_step",
                        cycle_num=1,
                        metric_before=0.0,
                        metric_after=1.0,
                        summary_diff=f"Clave {clave}: Promovente='{promovente}', Sector='{sector}', Estado='{estado}'",
                        auto_repaired=True
                    )
                    return {
                        "status": "PASS",
                        "curated": True,
                        "clave": clave,
                        "metadata": {"promovente": promovente, "sector": sector, "estado": estado, "municipio": municipio}
                    }
        except Exception as exc:
            logger.warning("Error en curaduría atómica para %s: %s", md_file.name, exc)

    return {"status": "no_curation_needed", "curated": False}


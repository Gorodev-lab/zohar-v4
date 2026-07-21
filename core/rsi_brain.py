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
    summary_diff: str
) -> Path:
    """
    Guarda automáticamente una lección aprendida en second_brain/03_Inferences/rsi_learning_<target>.md.
    """
    inferences_dir = SECOND_BRAIN_DIR / "03_Inferences"
    inferences_dir.mkdir(parents=True, exist_ok=True)
    target_stem = Path(target_file).stem
    note_path = inferences_dir / f"rsi_learning_{target_stem}.md"

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"\n## [RSI LEARNING] Ciclo {cycle_num} - {ts}\n"
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

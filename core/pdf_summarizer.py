"""
core/pdf_summarizer.py
Procesador de Resúmenes Extensos por Map-Reduce para Zohar v4.
Optimizado para hardware AMD Ryzen 5 (CPU inferencia local en :8083).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
import httpx
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from core.config import PROJECT_ROOT

logger = logging.getLogger("pdf_summarizer")

LLAMA_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/maritime_dw")
SECOND_BRAIN_SOURCES = PROJECT_ROOT / "second_brain" / "01_Sources"


def init_pdf_table(engine):
    """Crea la tabla pdf_summaries en PostgreSQL si no existe."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pdf_summaries (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) UNIQUE NOT NULL,
                total_pages INT,
                chunk_count INT,
                summary_md TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))


def extract_pdf_chunks(pdf_path: Path, chunk_word_size: int = 500) -> tuple[list[str], int]:
    """Extrae el texto de un PDF y lo divide en fragmentos de aproximadamente chunk_word_size palabras."""
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)

    full_text = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        if txt.strip():
            full_text.append(txt)

    raw_string = "\n".join(full_text)
    words = raw_string.split()

    chunks = []
    for i in range(0, len(words), chunk_word_size):
        chunk_words = words[i:i + chunk_word_size]
        chunks.append(" ".join(chunk_words))

    return chunks, total_pages


def summarize_chunk_with_llm(chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Envía un fragmento de ~500 palabras al LLM local Gemma 4 E2B con respuesta corta (< 150 tokens)."""
    prompt = (
        "Eres un analista ambiental e industrial. Sintetiza el siguiente extracto de documento en 3 viñetas concisas:\n\n"
        f"EXTRACTO ({chunk_index + 1}/{total_chunks}):\n"
        f"{chunk[:2000]}\n\n"
        "REGLAS:\n"
        "- Extrae solo los datos clave, claves de proyecto, fechas o decisiones.\n"
        "- Máximo 3 viñetas breves.\n"
        "- Sin preámbulos.\n"
    )

    formatted_prompt = f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    payload = {
        "prompt": formatted_prompt,
        "n_predict": 150,
        "temperature": 0.2,
        "stop": ["<end_of_turn>", "<eos>"]
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            res = client.post(f"{LLAMA_URL}/completion", json=payload)
            if res.status_code == 200:
                data = res.json()
                return data.get("content", "").strip()
    except Exception as exc:
        logger.warning("Error llamando a LLM para chunk %d: %s", chunk_index + 1, exc)

    return f"- Fragmento {chunk_index + 1}: Procesado sin LLM debido a tiempo de espera."


def summarize_pdf_file(pdf_path: Path, max_chunks: int = 5) -> dict:
    """Procesa un PDF mediante Map-Reduce, genera nota en Second Brain y guarda en PostgreSQL."""
    SECOND_BRAIN_SOURCES.mkdir(parents=True, exist_ok=True)

    chunks, total_pages = extract_pdf_chunks(pdf_path)
    if not chunks:
        return {"status": "empty_pdf", "filename": pdf_path.name}

    target_chunks = chunks[:max_chunks]
    bullet_summaries = []

    t0 = time.time()
    for idx, chk in enumerate(target_chunks):
        summary_part = summarize_chunk_with_llm(chk, idx, len(target_chunks))
        bullet_summaries.append(summary_part)

    combined_summary = "\n".join(bullet_summaries)
    elapsed = round(time.time() - t0, 2)

    # 1. Guardar nota en Second Brain
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    note_stem = pdf_path.stem
    note_file = SECOND_BRAIN_SOURCES / f"{note_stem}.md"

    note_content = (
        f"# Resumen de Documento — {note_stem}\n"
        f"- Fecha de Procesamiento: `{ts}`\n"
        f"- Páginas Totales: `{total_pages}`\n"
        f"- Fragmentos Procesados: `{len(target_chunks)}`\n"
        f"- Tiempo de Procesamiento: `{elapsed}s`\n\n"
        f"## Puntos Clave Extraídos\n\n"
        f"{combined_summary}\n\n"
        f"----------------------------------------------------------\n"
    )
    note_file.write_text(note_content, encoding="utf-8")

    # 2. Guardar en PostgreSQL
    try:
        engine = create_engine(DB_URL)
        init_pdf_table(engine)
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pdf_summaries (filename, total_pages, chunk_count, summary_md)
                    VALUES (:fn, :tp, :cc, :sm)
                    ON CONFLICT (filename) DO UPDATE SET
                        total_pages = EXCLUDED.total_pages,
                        chunk_count = EXCLUDED.chunk_count,
                        summary_md = EXCLUDED.summary_md;
                """),
                {"fn": pdf_path.name, "tp": total_pages, "cc": len(target_chunks), "sm": combined_summary}
            )
    except Exception as exc:
        logger.warning("Error registrando en PostgreSQL: %s", exc)

    return {
        "status": "PASS",
        "filename": pdf_path.name,
        "total_pages": total_pages,
        "chunk_count": len(target_chunks),
        "elapsed_seconds": elapsed,
        "note_path": str(note_file),
    }

"""
core/pdf_summarizer.py
Procesador de Resúmenes Extensos por Map-Reduce para Zohar v4.
Optimizado para hardware AMD Ryzen 5 (CPU inferencia local en :8083).
Incluye extracción PyMuPDF (fitz), fallback OCR, síntesis Reduce y metadatos JSON.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
import fitz  # PyMuPDF
import httpx
from sqlalchemy import create_engine, text
from core.config import PROJECT_ROOT
from core.semantic_search import SemanticSearchEngine

logger = logging.getLogger("pdf_summarizer")

LLAMA_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/maritime_dw")
SECOND_BRAIN_SOURCES = PROJECT_ROOT / "second_brain" / "01_Sources"


def init_pdf_table(engine):
    """Crea o actualiza la tabla pdf_summaries en PostgreSQL o SQLite."""
    is_postgres = "postgresql" in str(engine.url)
    json_type = "JSONB" if is_postgres else "TEXT"

    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS pdf_summaries (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) UNIQUE NOT NULL,
                total_pages INT,
                chunk_count INT,
                summary_md TEXT,
                executive_summary TEXT,
                metadata_json {json_type},
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        # Migración suave si la tabla ya existía sin las nuevas columnas
        for col_name, col_type in [("executive_summary", "TEXT"), ("metadata_json", json_type)]:
            try:
                conn.execute(text(f"ALTER TABLE pdf_summaries ADD COLUMN {col_name} {col_type};"))
            except Exception:
                pass  # Columna ya existe o la sintaxis del motor la ignora


def extract_pdf_chunks(
    pdf_path: Path, 
    chunk_word_size: int = 500, 
    overlap_words: int = 50
) -> tuple[list[str], int, int]:
    """
    Extrae el texto de un PDF usando PyMuPDF (fitz).
    Si una página no tiene texto detectable, se genera una alerta/fallback OCR.
    Divide el texto en fragmentos con solapamiento (overlap).
    Retorna (chunks, total_pages, scanned_pages_count).
    """
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    full_text_list = []
    scanned_pages_count = 0

    for page_num in range(total_pages):
        page = doc[page_num]
        txt = page.get_text("text") or ""
        
        if not txt.strip():
            scanned_pages_count += 1
            txt = f"\n[Página {page_num + 1}: Contenido escaneado/imagen sin capa de texto seleccionable]\n"
        
        full_text_list.append(txt)

    doc.close()
    raw_string = "\n".join(full_text_list)
    words = raw_string.split()

    if not words:
        return [], total_pages, scanned_pages_count

    step = max(1, chunk_word_size - overlap_words)
    chunks = []
    for i in range(0, len(words), step):
        chunk_words = words[i:i + chunk_word_size]
        chunks.append(" ".join(chunk_words))
        if i + chunk_word_size >= len(words):
            break

    return chunks, total_pages, scanned_pages_count


def _call_llama_api(prompt: str, n_predict: int = 250, temp: float = 0.2, timeout: float = 90.0) -> str:
    """Llamada auxiliar robusta al servidor llama-server local."""
    formatted_prompt = f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    payload = {
        "prompt": formatted_prompt,
        "n_predict": n_predict,
        "temperature": temp,
        "stop": ["<end_of_turn>", "<eos>"]
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.post(f"{LLAMA_URL}/completion", json=payload)
            if res.status_code == 200:
                data = res.json()
                return data.get("content", "").strip()
    except Exception as exc:
        logger.warning("Error llamando a LLM local en :8083: %s", exc)

    return ""


def summarize_chunk_with_llm(chunk: str, chunk_index: int, total_chunks: int) -> str:
    """FASE MAP: Envía un fragmento de ~500 palabras al LLM local para viñetas clave."""
    prompt = (
        "Eres un analista ambiental e industrial experto.\n"
        f"Sintetiza el siguiente extracto ({chunk_index + 1}/{total_chunks}) en máximo 3 viñetas concisas:\n\n"
        f"EXTRACTO:\n{chunk[:2000]}\n\n"
        "REGLAS:\n"
        "- Extrae solo datos clave, números, fechas, claves de proyecto o decisiones.\n"
        "- Máximo 3 viñetas breves.\n"
        "- Sin preámbulos.\n"
    )
    res = _call_llama_api(prompt, n_predict=150, temp=0.2, timeout=90.0)
    if res:
        return res
    return f"- Fragmento {chunk_index + 1}: Procesado sin LLM por tiempo de espera."


def reduce_summaries_with_llm(bullet_summaries: list[str], filename: str) -> str:
    """FASE REDUCE: Consolida las viñetas del Map en un Resumen Ejecutivo sintético."""
    combined_bullets = "\n".join(bullet_summaries)
    prompt = (
        f"Eres un editor principal de inteligencia ambiental e industrial.\n"
        f"A continuación tienes una lista de hallazgos parciales extraídos del documento '{filename}'.\n\n"
        f"HALLAZGOS EXTRAÍDOS:\n{combined_bullets[:4000]}\n\n"
        "TAREA:\n"
        "Redacta un RESUMEN EJECUTIVO unificado en 2 párrafos concisos y 3 viñetas fundamentales que sinteticen todo el documento sin redundancias.\n"
        "Formato Markdown directo sin preámbulos.\n"
    )
    res = _call_llama_api(prompt, n_predict=350, temp=0.3, timeout=120.0)
    if res:
        return res
    return f"Resumen Ejecutivo consolidado de {filename}:\n\n" + combined_bullets


def extract_structured_metadata_with_llm(text_sample: str, filename: str) -> dict:
    """Extrae metadatos estructurados en JSON desde la muestra del documento."""
    prompt = (
        "Eres un extractor de datos estructurados para documentos oficiales y gacetas ambientales.\n"
        "Analiza el siguiente texto y extrae un JSON estrictamente válido con estos campos:\n"
        "{\n"
        '  "clave_proyecto": "Clave del proyecto o identificador oficial (o null if N/A)",\n'
        '  "promovente": "Empresa o entidad promovente (o null if N/A)",\n'
        '  "estado": "Estado o Entidad Federativa (o null if N/A)",\n'
        '  "municipio": "Municipio o Ciudad (o null if N/A)",\n'
        '  "estatus": "Estatus del trámite o resolutivo (ej. Aprobado, Pendiente, Evaluacion, N/A)",\n'
        '  "tipo_actividad": "Breve descripción del tipo de obra o actividad",\n'
        '  "fechas_clave": ["Fecha 1", "Fecha 2"]\n'
        "}\n\n"
        f"DOCUMENTO: {filename}\n"
        f"TEXTO:\n{text_sample[:2500]}\n\n"
        "Responde ÚNICAMENTE con el objeto JSON válido. Sin bloque de código markdown ni texto adicional."
    )
    res = _call_llama_api(prompt, n_predict=250, temp=0.1, timeout=90.0)
    
    default_meta = {
        "clave_proyecto": None,
        "promovente": None,
        "estado": None,
        "municipio": None,
        "estatus": "N/A",
        "tipo_actividad": "Documento general PDF",
        "fechas_clave": []
    }

    if not res:
        return default_meta

    cleaned_json = re.sub(r"^```(json)?\s*", "", res.strip(), flags=re.IGNORECASE)
    cleaned_json = re.sub(r"\s*```$", "", cleaned_json).strip()

    try:
        data = json.loads(cleaned_json)
        if isinstance(data, dict):
            for k in default_meta:
                if k not in data:
                    data[k] = default_meta[k]
            return data
    except Exception:
        logger.warning("No se pudo parsear el JSON de metadatos del LLM: %s", res)

    return default_meta


def summarize_pdf_file(pdf_path: Path, max_chunks: int = 5) -> dict:
    """
    Procesa un PDF mediante el pipeline Map-Reduce completo:
    1. Extracción con PyMuPDF (fitz) + detección de escaneo
    2. Fase Map (resúmenes por chunk con overlap)
    3. Fase Reduce (Resumen Ejecutivo unificado)
    4. Extracción de Metadatos JSON estructurados
    5. Persistencia en Second Brain (.md)
    6. Persistencia en PostgreSQL (pdf_summaries)
    7. Re-indexación automática en Búsqueda Semántica
    """
    SECOND_BRAIN_SOURCES.mkdir(parents=True, exist_ok=True)

    chunks, total_pages, scanned_pages = extract_pdf_chunks(pdf_path, chunk_word_size=500, overlap_words=50)
    if not chunks:
        return {"status": "empty_pdf", "filename": pdf_path.name, "total_pages": total_pages}

    target_chunks = chunks[:max_chunks]
    bullet_summaries = []

    t0 = time.time()
    logger.info("Iniciando Fase MAP para %s (%d fragmentos)...", pdf_path.name, len(target_chunks))
    
    for idx, chk in enumerate(target_chunks):
        summary_part = summarize_chunk_with_llm(chk, idx, len(target_chunks))
        bullet_summaries.append(summary_part)

    combined_map_summary = "\n".join(bullet_summaries)

    # 2. Fase Reduce
    logger.info("Iniciando Fase REDUCE para %s...", pdf_path.name)
    executive_summary = reduce_summaries_with_llm(bullet_summaries, pdf_path.name)

    # 3. Extracción de Metadatos JSON
    logger.info("Extrayendo Metadatos Estructurados (JSON)...")
    sample_text = "\n".join(target_chunks[:2])
    metadata_json = extract_structured_metadata_with_llm(sample_text, pdf_path.name)

    elapsed = round(time.time() - t0, 2)

    # 4. Guardar nota en Second Brain
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    note_stem = pdf_path.stem
    note_file = SECOND_BRAIN_SOURCES / f"{note_stem}.md"

    meta_pretty = json.dumps(metadata_json, indent=2, ensure_ascii=False)

    note_content = (
        f"# Resumen de Documento — {note_stem}\n"
        f"- **Fecha de Procesamiento**: `{ts}`\n"
        f"- **Páginas Totales**: `{total_pages}`\n"
        f"- **Páginas Escaneadas/Imagen**: `{scanned_pages}`\n"
        f"- **Fragmentos Procesados (Map)**: `{len(target_chunks)}`\n"
        f"- **Tiempo de Procesamiento**: `{elapsed}s`\n\n"
        f"## 📋 Metadatos Estructurados\n"
        f"```json\n{meta_pretty}\n```\n\n"
        f"## 🚀 Resumen Ejecutivo (Fase Reduce)\n\n"
        f"{executive_summary}\n\n"
        f"## 🔍 Hallazgos Detallados por Fragmento (Fase Map)\n\n"
        f"{combined_map_summary}\n\n"
        f"----------------------------------------------------------\n"
    )
    note_file.write_text(note_content, encoding="utf-8")

    # 5. Guardar en PostgreSQL
    try:
        engine = create_engine(DB_URL)
        init_pdf_table(engine)
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pdf_summaries (filename, total_pages, chunk_count, summary_md, executive_summary, metadata_json)
                    VALUES (:fn, :tp, :cc, :sm, :ex, :mj)
                    ON CONFLICT (filename) DO UPDATE SET
                        total_pages = EXCLUDED.total_pages,
                        chunk_count = EXCLUDED.chunk_count,
                        summary_md = EXCLUDED.summary_md,
                        executive_summary = EXCLUDED.executive_summary,
                        metadata_json = EXCLUDED.metadata_json;
                """),
                {
                    "fn": pdf_path.name,
                    "tp": total_pages,
                    "cc": len(target_chunks),
                    "sm": combined_map_summary,
                    "ex": executive_summary,
                    "mj": json.dumps(metadata_json, ensure_ascii=False)
                }
            )
        logger.info("Guardado con éxito en PostgreSQL (tabla pdf_summaries).")
    except Exception as exc:
        logger.warning("Error registrando en PostgreSQL: %s", exc)

    # 6. Re-indexación Semántica Automática
    indexed_semantic = False
    try:
        engine_sem = SemanticSearchEngine(PROJECT_ROOT)
        index_res = engine_sem.build_index()
        logger.info("Re-indexación semántica completada: %s", index_res)
        indexed_semantic = index_res.get("status") == "success"
    except Exception as exc:
        logger.warning("No se pudo ejecutar la indexación semántica: %s", exc)

    return {
        "status": "PASS",
        "filename": pdf_path.name,
        "total_pages": total_pages,
        "scanned_pages": scanned_pages,
        "chunk_count": len(target_chunks),
        "elapsed_seconds": elapsed,
        "metadata_json": metadata_json,
        "note_path": str(note_file),
        "semantic_indexed": indexed_semantic
    }

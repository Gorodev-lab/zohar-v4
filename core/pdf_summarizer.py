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


def extract_pdf_prefix(pdf_path: Path, max_pages: int = 4) -> tuple[str, int, int]:
    """
    Abre el PDF y extrae el texto de las primeras `max_pages` páginas.
    Retorna (texto_concatenado, total_paginas, paginas_escaneadas_count).
    """
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    full_text_list = []
    scanned_pages_count = 0

    pages_to_read = min(total_pages, max_pages)
    for page_num in range(pages_to_read):
        page = doc[page_num]
        txt = page.get_text("text") or ""
        
        if not txt.strip():
            scanned_pages_count += 1
            txt = f"\n[Página {page_num + 1}: Contenido escaneado/imagen sin capa de texto seleccionable]\n"
        
        full_text_list.append(txt)

    # Contar las páginas escaneadas reales del resto del documento de forma ultra-rápida
    for page_num in range(pages_to_read, total_pages):
        page = doc[page_num]
        txt = page.get_text("text") or ""
        if not txt.strip():
            scanned_pages_count += 1

    doc.close()
    
    concatenated_text = "\n".join(full_text_list)
    return concatenated_text, total_pages, scanned_pages_count


def extract_structured_summary_and_metadata_with_llm(text_prefix: str, filename: str) -> dict:
    """Extrae el resumen ejecutivo, puntos clave y metadatos en un solo paso LLM."""
    prompt = (
        "Eres un analista ambiental e industrial experto y un extractor de datos estructurados para documentos oficiales de SEMARNAT.\n"
        "Analiza el siguiente extracto del documento y genera un objeto JSON estrictamente válido.\n\n"
        "Instrucciones de formato:\n"
        "Genera un JSON con exactamente estas tres llaves en el primer nivel:\n"
        "1. \"resumen_ejecutivo\": Un texto unificado en 2 párrafos concisos que sintetice la propuesta y hallazgos principales.\n"
        "2. \"puntos_clave\": Una lista (array) de 3 a 5 viñetas concisas con datos clave (números, decisiones, fechas, impactos).\n"
        "3. \"metadatos\": Un objeto con las siguientes llaves:\n"
        "   - \"clave_proyecto\": Clave oficial del proyecto o identificador de SEMARNAT (o null si no aplica)\n"
        "   - \"promovente\": Empresa o entidad promovente (o null si no aplica)\n"
        "   - \"estado\": Estado o Entidad Federativa (o null si no aplica)\n"
        "   - \"municipio\": Municipio o Ciudad (o null si no aplica)\n"
        "   - \"estatus\": Estatus del trámite o resolutivo (ej. Aprobado, Pendiente, Evaluacion, o N/A)\n"
        "   - \"tipo_actividad\": Breve descripción del tipo de obra o actividad\n"
        "   - \"fechas_clave\": Una lista de fechas importantes mencionadas en el texto (o un array vacío)\n\n"
        f"DOCUMENTO: {filename}\n"
        f"EXTRACTO DEL DOCUMENTO (Primeras páginas):\n{text_prefix[:6000]}\n\n"
        "Responde ÚNICAMENTE con el objeto JSON válido. No incluyas preámbulos, explicaciones ni bloques de código markdown."
    )
    
    # Usamos n_predict=600 para dar espacio a la generación del JSON completo
    res = _call_llama_api(prompt, n_predict=600, temp=0.1, timeout=240.0)
    
    default_res = {
        "resumen_ejecutivo": f"Resumen ejecutivo no disponible para {filename}.",
        "puntos_clave": [f"Documento {filename} procesado sin detalles del LLM."],
        "metadatos": {
            "clave_proyecto": None,
            "promovente": None,
            "estado": None,
            "municipio": None,
            "estatus": "N/A",
            "tipo_actividad": "Documento general PDF",
            "fechas_clave": []
        }
    }
    
    if not res:
        return default_res

    # Limpiar posibles bloques de código de markdown ```json
    cleaned_json = re.sub(r"^```(json)?\s*", "", res.strip(), flags=re.IGNORECASE)
    cleaned_json = re.sub(r"\s*```$", "", cleaned_json).strip()

    try:
        data = json.loads(cleaned_json)
        if isinstance(data, dict):
            # Rellenar llaves faltantes con valores por defecto
            if "resumen_ejecutivo" not in data:
                data["resumen_ejecutivo"] = default_res["resumen_ejecutivo"]
            if "puntos_clave" not in data:
                data["puntos_clave"] = default_res["puntos_clave"]
            if "metadatos" not in data or not isinstance(data["metadatos"], dict):
                data["metadatos"] = default_res["metadatos"]
            else:
                for k in default_res["metadatos"]:
                    if k not in data["metadatos"]:
                        data["metadatos"][k] = default_res["metadatos"][k]
            return data
    except Exception as exc:
        logger.warning("No se pudo parsear el JSON de extracción unificada: %s. Respuesta original: %s", exc, res)

    return default_res


def summarize_pdf_file(pdf_path: Path, max_chunks: int = 5) -> dict:
    """
    Procesa un PDF en un único pase (Single-Pass) para Zohar v4 MVP:
    1. Extracción con PyMuPDF (fitz) de las primeras 4 páginas
    2. Llamada unificada al LLM para Resumen + Metadatos
    3. Persistencia en Second Brain (.md)
    4. Persistencia en PostgreSQL (pdf_summaries)
    5. Re-indexación automática en Búsqueda Semántica
    """
    SECOND_BRAIN_SOURCES.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    logger.info("Iniciando extracción Single-Pass para %s...", pdf_path.name)
    
    text_prefix, total_pages, scanned_pages = extract_pdf_prefix(pdf_path, max_pages=3)
    if not text_prefix.strip() and total_pages == 0:
        return {"status": "empty_pdf", "filename": pdf_path.name, "total_pages": total_pages}

    logger.info("Llamando al LLM local para generación de Resumen y Metadatos...")
    result_data = extract_structured_summary_and_metadata_with_llm(text_prefix, pdf_path.name)
    
    executive_summary = result_data.get("resumen_ejecutivo", "")
    puntos_clave = result_data.get("puntos_clave", [])
    metadata_json = result_data.get("metadatos", {})
    
    combined_map_summary = "\n".join([f"- {bullet}" for bullet in puntos_clave])
    
    elapsed = round(time.time() - t0, 2)

    # Guardar nota en Second Brain
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    note_stem = pdf_path.stem
    note_file = SECOND_BRAIN_SOURCES / f"{note_stem}.md"

    meta_pretty = json.dumps(metadata_json, indent=2, ensure_ascii=False)

    note_content = (
        f"# Resumen de Documento — {note_stem}\n"
        f"- **Fecha de Procesamiento**: `{ts}`\n"
        f"- **Páginas Totales**: `{total_pages}`\n"
        f"- **Páginas Escaneadas/Imagen**: `{scanned_pages}`\n"
        f"- **Fragmentos Procesados**: `1 (Single-Pass)`\n"
        f"- **Tiempo de Procesamiento**: `{elapsed}s`\n\n"
        f"## 📋 Metadatos Estructurados\n"
        f"```json\n{meta_pretty}\n```\n\n"
        f"## 🚀 Resumen Ejecutivo\n\n"
        f"{executive_summary}\n\n"
        f"## 🔍 Puntos Clave Extraídos\n\n"
        f"{combined_map_summary}\n\n"
        f"----------------------------------------------------------\n"
    )
    note_file.write_text(note_content, encoding="utf-8")

    # Guardar en PostgreSQL
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
                    "cc": 1,
                    "sm": combined_map_summary,
                    "ex": executive_summary,
                    "mj": json.dumps(metadata_json, ensure_ascii=False)
                }
            )
        logger.info("Guardado con éxito en PostgreSQL (tabla pdf_summaries).")
    except Exception as exc:
        logger.warning("Error registrando en PostgreSQL: %s", exc)

    # Re-indexación Semántica Automática
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
        "chunk_count": 1,
        "elapsed_seconds": elapsed,
        "metadata_json": metadata_json,
        "note_path": str(note_file),
        "semantic_indexed": indexed_semantic
    }


def batch_summarize_unprocessed_pdfs_gen(max_files: int = 5, max_chunks: int = 4):
    """
    Generador SSE para procesar de forma secuencial los PDFs pendientes de resumen.
    Filtra archivos que ya tengan nota en `second_brain/01_Sources/`.
    """
    DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
    estudios_dir = DOWNLOADS_DIR / "estudios"
    gacetas_dir = DOWNLOADS_DIR / "gacetas"

    candidates = []
    for dir_path in [estudios_dir, gacetas_dir]:
        if dir_path.exists():
            for pdf in dir_path.glob("*.pdf"):
                note_file = SECOND_BRAIN_SOURCES / f"{pdf.stem}.md"
                if not note_file.exists():
                    candidates.append(pdf)

    total_candidates = len(candidates)
    if total_candidates == 0:
        yield {
            "status": "complete",
            "pct": 100,
            "msg": "No hay PDFs pendientes por resumir en el corpus.",
            "processed": 0,
        }
        return

    to_process = candidates[:max_files]
    total_to_process = len(to_process)

    yield {
        "status": "running",
        "pct": 0,
        "msg": f"Iniciando resumen batch de {total_to_process} PDFs pendientes (de {total_candidates} encontrados)...",
    }

    processed_count = 0
    for idx, pdf_file in enumerate(to_process):
        pct = round((idx / total_to_process) * 100, 1)
        yield {
            "status": "running",
            "pct": pct,
            "msg": f"Procesando ({idx + 1}/{total_to_process}): {pdf_file.name}...",
            "file": pdf_file.name,
        }

        try:
            res = summarize_pdf_file(pdf_file, max_chunks=max_chunks)
            processed_count += 1
            yield {
                "status": "running",
                "pct": round(((idx + 1) / total_to_process) * 100, 1),
                "msg": f"✓ Completado ({idx + 1}/{total_to_process}): {pdf_file.name} ({res.get('elapsed_seconds', 0)}s)",
                "file": pdf_file.name,
            }
        except Exception as exc:
            logger.error("Error procesando %s en batch: %s", pdf_file.name, exc)
            # Mover archivo a cuarentena para evitar bucles infinitos de reintento
            try:
                corrupt_dir = pdf_file.parent / "_corruptos"
                corrupt_dir.mkdir(exist_ok=True)
                pdf_file.rename(corrupt_dir / pdf_file.name)
                logger.info("PDF corrupto/ilegible movido a cuarentena: %s", corrupt_dir / pdf_file.name)
            except Exception as move_exc:
                logger.error("No se pudo mover el PDF corrupto a cuarentena: %s", move_exc)

            yield {
                "status": "warning",
                "pct": round(((idx + 1) / total_to_process) * 100, 1),
                "msg": f"⚠️ Error en {pdf_file.name} (movido a cuarentena): {exc}",
                "file": pdf_file.name,
            }

    yield {
        "status": "complete",
        "pct": 100,
        "msg": f"Procesamiento batch finalizado. {processed_count}/{total_to_process} PDFs resumidos exitosamente.",
        "processed": processed_count,
    }

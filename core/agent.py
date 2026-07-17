"""
core/agent.py
Implementación del agente de IA interactivo de Zohar v4.
Soporta Loop de React (Function Calling) local con ejecución de herramientas y parsing XML.
"""

from __future__ import annotations

import os
import re
import json
import logging
from pathlib import Path
from typing import Optional, Any, Callable, Generator

logger = logging.getLogger(__name__)

# Directorios Base
BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
EXTRACTIONS_DIR = BASE_DIR / "extractions"
DATA_DIR = BASE_DIR / "data"

RESUMENES_DIR = DOWNLOADS_DIR / "resumenes"
ESTUDIOS_DIR = DOWNLOADS_DIR / "estudios"
RESOLUTIVOS_DIR = DOWNLOADS_DIR / "resolutivos"
GACETAS_DIR = DOWNLOADS_DIR / "gacetas"


# ---------------------------------------------------------------------------
# Definición de Herramientas Reales del Agente
# ---------------------------------------------------------------------------

def run_db_query(sql_query: str) -> str:
    """
    Ejecuta una consulta SQL de tipo SELECT en las tablas 'semarnat_projects' o
    'project_evaluations' de PostgreSQL y devuelve los resultados en una tabla Markdown.
    """
    query_clean = sql_query.strip().lower()
    if not query_clean.startswith("select"):
        return "Error: Solo se permiten consultas de lectura (SELECT)."
    
    # Prevenir comandos DDL/DML de alteración o destrucción
    for forbidden in ["insert", "update", "delete", "drop", "truncate", "create", "alter", "grant", "revoke"]:
        if re.search(rf"\b{forbidden}\b", query_clean):
            return f"Error: No se permite el comando prohibido '{forbidden}'."

    from sqlalchemy import create_engine, text
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
    try:
        engine = create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            result = conn.execute(text(sql_query))
            cols = list(result.keys())
            rows = result.fetchall()
            if not rows:
                return "Consulta completada con éxito. 0 filas retornadas."
            
            # Formatear a tabla Markdown
            output = []
            output.append(" | ".join(cols))
            output.append("-|-".join(["---" for _ in cols]))
            for r in rows[:20]:  # Limitar a 20 filas para no saturar el contexto
                output.append(" | ".join([str(val) for val in r]))
            if len(rows) > 20:
                output.append(f"\n... (truncado, {len(rows) - 20} filas más) ...")
            return "\n".join(output)
    except Exception as exc:
        return f"Error ejecutando consulta SQL: {exc}"


def run_second_brain_search(query: str) -> str:
    """
    Realiza una búsqueda semántica de alta precisión utilizando embeddings locales
    en las notas del Second Brain.
    """
    from core.semantic_search import SemanticSearchEngine
    try:
        search_engine = SemanticSearchEngine(BASE_DIR)
        results = search_engine.search(query, limit=5)
        if not results:
            return "No se encontraron notas semánticamente similares en el Second Brain."
        
        output = ["Resultados de búsqueda semántica:"]
        for res in results:
            output.append(
                f"- **{res['title']}** (Categoría: {res['category']}) - Similitud: {res['pct']}% [Nota: [[{res['title']}]]]"
            )
        return "\n".join(output)
    except Exception as exc:
        return f"Error en búsqueda semántica: {exc}"


def run_ocr_extraction(pdf_name: str) -> str:
    """
    Localiza un PDF en el corpus, extrae su texto página por página aplicando OCR híbrido
    y lo guarda en Markdown en la carpeta de extracciones.
    """
    from core.pdf_processor import iter_pages_as_markdown
    
    # Localizar archivo
    pdf_path = None
    for folder in [RESUMENES_DIR, ESTUDIOS_DIR, RESOLUTIVOS_DIR, GACETAS_DIR]:
        candidate = folder / pdf_name
        if candidate.exists():
            pdf_path = candidate
            break
            
    if not pdf_path:
        return f"Error: PDF '{pdf_name}' no encontrado en el corpus."
        
    try:
        pages_md = []
        for page_num, total, md_text, is_scanned in iter_pages_as_markdown(pdf_path):
            pages_md.append(md_text)
            
        EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        md_filename = pdf_path.stem + ".md"
        md_path = EXTRACTIONS_DIR / md_filename
        
        full_md = (
            f"# {pdf_path.stem}\n\n"
            f"_Extraído de: {pdf_name}_\n\n"
            + "\n\n---\n\n".join(pages_md)
        )
        md_path.write_text(full_md, encoding="utf-8")
        
        return (
            f"Extracción completada. Archivo guardado: extractions/{md_filename}\n"
            f"Páginas procesadas: {len(pages_md)}\n"
            f"Longitud: {len(full_md)} caracteres."
        )
    except Exception as exc:
        return f"Error extrayendo '{pdf_name}': {exc}"


def run_second_brain_sync() -> str:
    """
    Compila y actualiza todas las notas Markdown vinculadas en el Second Brain
    y actualiza el índice semántico de embeddings locales.
    """
    from core.second_brain import SecondBrainBuilder
    from core.semantic_search import SemanticSearchEngine
    try:
        builder = SecondBrainBuilder(BASE_DIR)
        stats = builder.build_vault()
        
        search_engine = SemanticSearchEngine(BASE_DIR)
        index_stats = search_engine.build_index()
        
        return (
            f"Sincronización del Second Brain completada con éxito.\n"
            f"- Proyectos en bóveda: {stats.get('total_proyectos', 0)}\n"
            f"- Notas totales indexadas en caché: {index_stats.get('total_cached', 0)}"
        )
    except Exception as exc:
        return f"Error sincronizando Second Brain: {exc}"


# Mapa de Herramientas
AGENT_TOOLS: dict[str, Callable[..., str]] = {
    "database_query": run_db_query,
    "second_brain_search": run_second_brain_search,
    "ocr_extraction": run_ocr_extraction,
    "second_brain_sync": run_second_brain_sync
}


# ---------------------------------------------------------------------------
# Clase Agente de Razonamiento (Loop React)
# ---------------------------------------------------------------------------

class ZoharAgent:
    def __init__(self, sys_prompt: str, history: list[dict]):
        self.sys_prompt = sys_prompt
        self.history = history
        self.max_iterations = 3

    def run(self, message: str) -> tuple[str, list[dict]]:
        """
        Inicia el ciclo de ejecución del agente.
        Retorna: (respuesta_final, tool_calls_log)
        """
        from core.llm_client import generate_completion

        # ---------------------------------------------------------------------------
        # Constante de truncado de resultados de herramientas
        # ---------------------------------------------------------------------------
        TOOL_RESULT_MAX_CHARS = 2000

        # Enriquecer el system prompt con las directrices del agente (separado en 2 bloques)
        # BLOQUE A: Formato de llamada a herramientas
        tool_format_block = (
            "\n\n## REGLAS DE HERRAMIENTAS\n"
            "Usa EXACTAMENTE este formato XML. Detente INMEDIATAMENTE después del </tool_call> — "
            "no escribas nada más. El sistema ejecutará la herramienta y te dará el resultado.\n"
            "<tool_call name=\"NOMBRE_HERRAMIENTA\">{\"argumento\": \"valor\"}</tool_call>\n\n"
        )

        # BLOQUE B: Reglas de decisión + ejemplos few-shot por herramienta
        tool_rules_block = (
            "## HERRAMIENTAS DISPONIBLES\n\n"

            # --- database_query ---
            "### 1. database_query\n"
            "CUÁNDO USAR: Preguntas sobre conteos, estadísticas, estados de trámites, proyectos por "
            "sector/estado/año, veredictos FAVORABLE/DESFAVORABLE/CONDICIONADO, scores de evaluación.\n"
            "CUÁNDO NO USAR: Para buscar el contenido narrativo de un proyecto — usa second_brain_search.\n"
            "Parámetro: sql_query (SELECT en semarnat_projects o project_evaluations ÚNICAMENTE)\n"
            "Valores de status: 'EN PROCESO', 'RESUELTO', 'INGRESADO'\n"
            "Valores de veredicto: 'FAVORABLE', 'DESFAVORABLE', 'CONDICIONADO', 'PENDIENTE'\n\n"
            "EJEMPLO 1 — Usuario pregunta estadísticas globales:\n"
            "Usuario: ¿Cuántos proyectos desfavorables hay registrados este año?\n"
            "Razonamiento: pregunta de conteo → database_query\n"
            "<tool_call name=\"database_query\">"
            "{\"sql_query\": \"SELECT COUNT(*) AS total FROM public.project_evaluations "
            "JOIN public.semarnat_projects USING(clave) WHERE veredicto='DESFAVORABLE' AND year=2026;\"}"
            "</tool_call>\n\n"
            "EJEMPLO 2 — Usuario pregunta por proyectos de un sector:\n"
            "Usuario: ¿Qué proyectos del sector acuícola están en proceso?\n"
            "Razonamiento: filtro por sector y status → database_query\n"
            "<tool_call name=\"database_query\">"
            "{\"sql_query\": \"SELECT clave, project_name, state, status FROM public.semarnat_projects "
            "WHERE sector ILIKE '%acuicola%' AND status='EN PROCESO' LIMIT 10;\"}"
            "</tool_call>\n\n"

            # --- second_brain_search ---
            "### 2. second_brain_search\n"
            "CUÁNDO USAR: Preguntas sobre el contenido narrativo de un proyecto, quién es el promovente, "
            "qué tipo de proyecto es, qué dice el estudio, la ubicación exacta, impactos descritos, "
            "o cuando el usuario menciona una clave de proyecto y quiere saber más de ella.\n"
            "CUÁNDO NO USAR: Para estadísticas o conteos — usa database_query.\n"
            "Parámetro: query (texto de búsqueda o clave de proyecto directamente)\n\n"
            "EJEMPLO 1 — Usuario pregunta por una clave específica:\n"
            "Usuario: ¿Qué sabes sobre el proyecto 03BS2026H0015?\n"
            "Razonamiento: pregunta narrativa sobre proyecto → second_brain_search\n"
            "<tool_call name=\"second_brain_search\">"
            "{\"query\": \"03BS2026H0015\"}"
            "</tool_call>\n\n"
            "EJEMPLO 2 — Usuario pregunta sobre un tema general:\n"
            "Usuario: ¿Hay proyectos de granjas porcinas en Sonora?\n"
            "Razonamiento: búsqueda temática narrativa → second_brain_search\n"
            "<tool_call name=\"second_brain_search\">"
            "{\"query\": \"granja porcina Sonora impacto ambiental\"}"
            "</tool_call>\n\n"

            # --- ocr_extraction ---
            "### 3. ocr_extraction\n"
            "CUÁNDO USAR: Solo cuando el usuario pide EXPLÍCITAMENTE leer, extraer o analizar el texto "
            "de un PDF específico que ya está en el corpus descargado.\n"
            "CUÁNDO NO USAR: Para buscar información general — usa second_brain_search primero.\n"
            "Parámetro: pdf_name (nombre COMPLETO del archivo incluyendo tipo y número, "
            "NUNCA solo la clave. Formato: CLAVE.tipo.NN.pdf)\n\n"
            "EJEMPLO — Usuario pide extraer un PDF específico:\n"
            "Usuario: Extrae el texto del estudio 03BS2026H0015.estudio.00.pdf\n"
            "Razonamiento: extracción explícita de PDF → ocr_extraction con nombre completo\n"
            "<tool_call name=\"ocr_extraction\">"
            "{\"pdf_name\": \"03BS2026H0015.estudio.00.pdf\"}"
            "</tool_call>\n\n"

            # --- second_brain_sync ---
            "### 4. second_brain_sync\n"
            "CUÁNDO USAR: SOLO cuando el usuario pide explícitamente 'sincronizar', 'actualizar', "
            "'recompilar' o 'reconstruir' la base de conocimiento o el Second Brain. "
            "Es una operación de mantenimiento, NO de consulta.\n"
            "CUÁNDO NO USAR: Para cualquier otra operación — usa las herramientas anteriores.\n"
            "Sin parámetros.\n\n"
            "EJEMPLO — Usuario pide mantenimiento del knowledge base:\n"
            "Usuario: Actualiza y sincroniza la base de conocimiento\n"
            "Razonamiento: petición de mantenimiento explícita → second_brain_sync\n"
            "<tool_call name=\"second_brain_sync\">{}</tool_call>\n\n"
        )

        agent_sys_prompt = self.sys_prompt + tool_format_block + tool_rules_block

        # Construir historial para el LLM
        prompt_builder = []
        for turn in self.history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role == "user":
                prompt_builder.append(f"Usuario: {content}")
            else:
                prompt_builder.append(f"Asistente: {content}")
        
        prompt_builder.append(f"Usuario: {message}")
        
        tool_calls_log = []
        
        for iteration in range(self.max_iterations):
            current_prompt = "\n".join(prompt_builder) + "\nAsistente:"
            
            res = generate_completion(
                prompt=current_prompt,
                system_prompt=agent_sys_prompt,
                response_json=False
            )
            
            if res.get("is_fallback"):
                return "Modo heurístico activo. Herramientas deshabilitadas sin LLM conectado.", tool_calls_log
                
            model_text = res.get("text", "").strip()
            
            # Buscar llamadas a herramientas (Soporta etiquetas XML y bloques de código JSON)
            tool_name = None
            args = {}
            
            match = re.search(r'<tool_call\s+name="([^"]+)">([\s\S]*?)</tool_call>', model_text)
            if match:
                tool_name = match.group(1).strip()
                args_raw = match.group(2).strip()
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = args_raw
            else:
                # Fallback: Buscar bloque Markdown de tipo json
                json_match = re.search(r'```json\s*([\s\S]*?)\s*```', model_text)
                if json_match:
                    try:
                        js_data = json.loads(json_match.group(1).strip())
                        tool_name = js_data.get("tool_name") or js_data.get("tool") or js_data.get("name")
                        args = js_data.get("parameters") or js_data.get("arguments") or js_data.get("params") or {}
                        
                        # Mapeos inteligentes de argumentos alternativos
                        if tool_name == "database_query" and isinstance(args, dict) and "query" in args and "sql_query" not in args:
                            args["sql_query"] = args["query"]
                        if tool_name == "second_brain_search" and isinstance(args, dict) and "search_query" in args and "query" not in args:
                            args["query"] = args["search_query"]
                    except Exception as exc:
                        logger.debug("Fallo al intentar parsear fallback JSON: %s", exc)

            if tool_name:
                    
                tool_result = ""
                if tool_name in AGENT_TOOLS:
                    try:
                        if isinstance(args, dict):
                            tool_result = AGENT_TOOLS[tool_name](**args)
                        else:
                            tool_result = AGENT_TOOLS[tool_name](args)
                    except Exception as e:
                        tool_result = f"Error ejecutando herramienta: {e}"
                else:
                    tool_result = f"Error: La herramienta '{tool_name}' no existe."
                    
                # Guardar en logs
                tool_calls_log.append({
                    "name": tool_name,
                    "arguments": args,
                    "result": tool_result
                })
                
                # Truncar resultados muy largos para no saturar el contexto del loop ReAct
                raw_result = tool_result
                if len(tool_result) > TOOL_RESULT_MAX_CHARS:
                    half = TOOL_RESULT_MAX_CHARS // 2
                    tool_result = (
                        f"[RESULTADO RESUMIDO — {len(raw_result)} caracteres totales, "
                        f"mostrando primeros {TOOL_RESULT_MAX_CHARS // 2} y últimos {TOOL_RESULT_MAX_CHARS // 2}]\n"
                        f"{raw_result[:half]}\n...[contenido omitido]...\n{raw_result[-half:]}\n"
                        f"[FIN DE RESULTADO — responde al usuario con la información disponible]"
                    )
                    logger.info(
                        "Resultado de herramienta '%s' truncado: %d → %d chars",
                        tool_name, len(raw_result), len(tool_result)
                    )
                
                # Alimentar el loop del agente
                prompt_builder.append(f"Asistente: {model_text}")
                prompt_builder.append(f"Sistema: [Resultado de '{tool_name}']: \n{tool_result}")
                continue
            else:
                return model_text, tool_calls_log
                
        return model_text, tool_calls_log

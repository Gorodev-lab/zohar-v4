#!/usr/bin/env python3
"""
auto_improver.py
================
Motor de Auto-Mejora Recursiva (RSI) para Zohar v4.
VERSIÓN GENÉRICA MULTI-OBJETIVO.

Uso básico (compatible con versión anterior):
    ./venv/bin/python auto_improver.py [--cycles N] [--dry-run]

Uso genérico (nuevo):
    ./venv/bin/python auto_improver.py \\
        --target-file infer.py \\
        --func-name extract_entities \\
        --eval-cmd "./venv/bin/python eval_zohar.py" \\
        --eval-metric score_float \\
        --patch-anchors "PROMPT DE EXTRACCIÓN,prompt =,try:" \\
        --max-window 80 \\
        --cycles 2

Métricas soportadas (--eval-metric):
  pytest_pass_rate  → parsea "N passed" → float N/total
  score_float       → parsea "SCORE: X.XXXX" → float
  exit_code         → 1.0 si returncode==0, else 0.0
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from core.rsi_brain import get_second_brain_context, save_rsi_learning

import httpx
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Defaults (compatibilidad con versión anterior — no rompe nada)
# ---------------------------------------------------------------------------

_DEFAULT_TARGET_FILE   = "scrapers/semarnat_downloader.py"
_DEFAULT_FUNC_NAME     = "_descargar_clave_gen"
_DEFAULT_EVAL_CMD      = "./venv/bin/pytest tests/test_scraper_pipeline.py -v --tb=short"
_DEFAULT_EVAL_METRIC   = "pytest_pass_rate"
_DEFAULT_PATCH_ANCHORS = ["PASO 5", "PASO 6", "PASO 4"]
_DEFAULT_MAX_WINDOW    = 50
_DEFAULT_MAX_CYCLES    = 3

BACKUP_SUFFIX = ".rsi_bak"
LOG_FILE      = Path("zohar_rsi.log")
GRAPHIFY_GRAPH = Path("graphify-out/graph.json")

# llama-server (Gemma 4 E2B en :8083)
LLAMA_URL   = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8083")
LLAMA_MODEL = os.getenv("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")

# ---------------------------------------------------------------------------
# Inventario de selectores del portal SINAT/Angular v18
# ---------------------------------------------------------------------------

ANGULAR_PORTAL_CONTEXT = (
    "PORTAL SEMARNAT SINAT — Angular v18 SPA\n"
    "URL: https://sinat.semarnat.gob.mx/portal-consulta\n\n"
    "DOM conocido (última auditoría):\n"
    "- Input búsqueda: CSS='input[type=text]', XPath='//input[contains(@placeholder,\"bitácora\")]'\n"
    "- Botón Buscar:   CSS='button.btn-primary', XPath='//button[contains(text(),\"Buscar\")]'\n"
    "- Botones descarga: '.descargas button', '[class*=\"descargas\"] button'\n"
    "- Fallback amplio: TAG_NAME=button filtrado (texto != 'Buscar')\n"
    "- Timeout Angular: 30-60s para enrutamiento post-búsqueda\n"
    "- Fallback red: network log CDP (performance log) para interceptar URLs PDF\n\n"
    "Comportamiento:\n"
    "- Hash fragment (#/portal-consulta) IGNORADO al inicio\n"
    "- Router Angular SIEMPRE aterriza en formulario primero\n"
    "- Botones de descarga solo aparecen DESPUÉS del enrutamiento\n"
    "- Error frecuente: StaleElementReferenceException en re-click\n"
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("zohar_rsi")


def log_jsonl(record: dict) -> None:
    """Append a JSON record to zohar_rsi.log."""
    record["ts"] = datetime.datetime.now().isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Graphify Oracle — betweenness centrality
# ---------------------------------------------------------------------------

def get_graphify_betweenness(func_name: str) -> float | None:
    """
    Calcula el degree centrality normalizado del nodo `func_name` en el grafo
    del codebase (graphify-out/graph.json).

    El graph.json de graphify almacena 'links' (aristas) y 'nodes' pero NO
    betweenness_centrality pre-computado — en su lugar calculamos el degree
    (número de conexiones entrantes + salientes) y lo normalizamos al rango
    [0.0, 1.0] dividiendo por el degree máximo del grafo.

    Retorna float normalizado [0.0..1.0] si se encuentra, None si no.
    """
    if not GRAPHIFY_GRAPH.exists():
        logger.debug("graphify-out/graph.json no encontrado, omitiendo betweenness proxy.")
        return None

    try:
        from collections import Counter
        data  = json.loads(GRAPHIFY_GRAPH.read_text(encoding="utf-8"))
        nodes = data.get("nodes", [])
        links = data.get("links", [])  # graphify usa 'links', no 'edges'

        if not links or not nodes:
            return None

        # Calcular degree de cada nodo ID
        degrees: Counter = Counter()
        for lnk in links:
            src = lnk.get("source", "")
            tgt = lnk.get("target", "")
            if src:
                degrees[src] += 1
            if tgt:
                degrees[tgt] += 1

        # Construir mapa id → label
        id_to_label = {n["id"]: str(n.get("label", "")) for n in nodes}
        label_to_id = {v.lower().rstrip("()"): k for k, v in id_to_label.items()}

        func_lower = func_name.lower().rstrip("()")
        max_degree = max(degrees.values()) if degrees else 1

        # Búsqueda exacta por label normalizado
        if func_lower in label_to_id:
            nid    = label_to_id[func_lower]
            degree = degrees.get(nid, 0)
            norm   = float(degree) / max(max_degree, 1)
            logger.info(
                "Graphify: nodo '%s' (id=%s) grado=%d → degree_centrality=%.4f",
                func_name, nid, degree, norm,
            )
            return norm

        # Búsqueda por substring
        for label_norm, nid in label_to_id.items():
            if func_lower in label_norm:
                degree = degrees.get(nid, 0)
                norm   = float(degree) / max(max_degree, 1)
                logger.info(
                    "Graphify: nodo '%s' encontrado por substring (label=%r, grado=%d) → degree_centrality=%.4f",
                    func_name, id_to_label[nid], degree, norm,
                )
                return norm

        logger.info("Graphify: nodo '%s' no encontrado en el grafo.", func_name)

    except Exception as exc:
        logger.warning("Error leyendo graph.json para degree centrality: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Memoria de ciclos — historial del log JSONL para el mismo objetivo
# ---------------------------------------------------------------------------

def get_cycle_history(target_file: str, func_name: str, n: int = 3) -> str:
    """
    Lee zohar_rsi.log y retorna un resumen comprimido de los últimos N ciclos
    del mismo (target_file, func_name) para inyectar en el prompt del LLM.

    Retorna string vacío si no hay historial relevante.
    """
    if not LOG_FILE.exists():
        return ""

    try:
        relevant = []
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Filtrar por objetivo (campos añadidos por esta versión genérica)
                rec_target = rec.get("target_file", "")
                rec_func   = rec.get("func_name", "")

                # Compatibilidad: registros antiguos sin target_file se ignoran
                if rec_target and rec_func:
                    if rec_target == target_file and rec_func == func_name:
                        event = rec.get("event", "")
                        if event in ("syntax_error", "cycle_success", "cycle_rollback",
                                     "no_change", "llm_empty", "no_python_block"):
                            relevant.append(rec)

        if not relevant:
            return ""

        # Tomar los últimos N
        recent = relevant[-n:]
        parts = []
        for i, rec in enumerate(recent, 1):
            event   = rec.get("event", "?")
            cycle   = rec.get("cycle", "?")
            err     = rec.get("error", "")
            m_bef   = rec.get("metric_before")
            m_aft   = rec.get("metric_after")
            diff_pv = rec.get("window_diff_preview", "")[:80]

            summary = f"CICLO {cycle}: {event}"
            if err:
                summary += f" — error: {err[:80]}"
            if m_bef is not None and m_aft is not None:
                summary += f" — métrica: {m_bef:.4f}→{m_aft:.4f}"
            if diff_pv:
                summary += f" — diff_preview: {diff_pv!r}"
            parts.append(summary)

        return "HISTORIAL ÚLTIMOS {} CICLOS (mismo objetivo):\n{}".format(
            len(parts), "\n".join(parts)
        )

    except Exception as exc:
        logger.warning("Error leyendo historial de ciclos: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Métrica genérica de evaluación
# ---------------------------------------------------------------------------

def run_eval(eval_cmd: str, eval_metric: str) -> tuple[float, str]:
    """
    Ejecuta eval_cmd y retorna (metric_value: float, output: str).

    Métricas:
      pytest_pass_rate  → "N passed" / total → float (total inferido del "N passed, M failed")
      score_float       → "SCORE: X.XXXX" → float
      exit_code         → 1.0 si returncode==0, 0.0 si no
    """
    try:
        result = subprocess.run(
            eval_cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=300.0,
        )
        output = (result.stderr or "") + "\n" + (result.stdout or "")
    except subprocess.TimeoutExpired:
        logger.error("Timeout ejecutando eval_cmd: %s", eval_cmd)
        return 0.0, "TIMEOUT"
    except Exception as exc:
        logger.error("Error ejecutando eval_cmd: %s — %s", eval_cmd, exc)
        return 0.0, str(exc)

    if eval_metric == "score_float":
        m = re.search(r"SCORE:\s*([0-9.]+)", output)
        if m:
            return float(m.group(1)), output
        logger.warning("No se encontró 'SCORE: X.XX' en la salida. Retornando 0.0")
        return 0.0, output

    if eval_metric == "pytest_pass_rate":
        m_pass = re.search(r"(\d+) passed", output)
        m_fail = re.search(r"(\d+) failed", output)
        m_err  = re.search(r"(\d+) error", output)
        n_pass  = int(m_pass.group(1)) if m_pass else 0
        n_fail  = int(m_fail.group(1)) if m_fail else 0
        n_err   = int(m_err.group(1))  if m_err  else 0
        total   = n_pass + n_fail + n_err
        rate    = float(n_pass) / max(total, 1)
        logger.info("pytest: %d/%d pasando (rate=%.4f)", n_pass, total, rate)
        return rate, output

    if eval_metric == "exit_code":
        return (1.0 if result.returncode == 0 else 0.0), output

    logger.warning("eval_metric desconocido: '%s'. Usando exit_code.", eval_metric)
    return (1.0 if result.returncode == 0 else 0.0), output


def run_tests() -> tuple[bool, str, int]:
    """
    Alias backward-compatible. Ejecuta la suite pytest del objetivo original.
    Retorna (all_passed: bool, output: str, n_passed: int).
    """
    rate, output = run_eval(_DEFAULT_EVAL_CMD, "pytest_pass_rate")
    m = re.search(r"(\d+) passed", output)
    n_passed = int(m.group(1)) if m else 0
    all_passed = rate >= 1.0
    return all_passed, output, n_passed


# ---------------------------------------------------------------------------
# Extracción y reinyección del bloque de función
# ---------------------------------------------------------------------------

def extract_function_block(source: str, func_name: str) -> tuple[str, int, int]:
    """
    Extrae el bloque completo de un método usando AST.
    Retorna (source_block, start_line_0idx, end_line_0idx_exclusive).
    """
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                start = node.lineno - 1      # 0-indexed
                end   = node.end_lineno      # 0-indexed exclusive
                block = "".join(lines[start:end])
                return block, start, end

    raise ValueError(f"Función `{func_name}` no encontrada en el archivo.")


def replace_function_block(source: str, func_name: str, new_block: str) -> str:
    """
    Reemplaza el bloque de la función `func_name` con `new_block`.
    Preserva todo lo demás del archivo.
    """
    _, start, end = extract_function_block(source, func_name)
    lines = source.splitlines(keepends=True)
    if not new_block.endswith("\n"):
        new_block += "\n"
    new_lines = lines[:start] + [new_block] + lines[end:]
    return "".join(new_lines)


# ---------------------------------------------------------------------------
# Ventana de parche quirúrgico
# ---------------------------------------------------------------------------

def extract_patch_window(
    func_body: str,
    anchors: list[str],
    fallback_lines: int = 90,
    max_window_lines: int = 100,
) -> tuple[str, str, str]:
    """
    Divide el cuerpo de la función en (head, window, tail).
    `window` es la ÚNICA parte que el LLM puede ver y reescribir.
    Prioriza los anclas en orden. Garantiza que `window` no exceda
    `max_window_lines` para evitar que el LLM de 2B pierda coherencia.
    """
    lines = func_body.splitlines(keepends=True)
    anchor_idx = None
    for a in anchors:
        for i, line in enumerate(lines):
            if a in line:
                anchor_idx = i
                break
        if anchor_idx is not None:
            break

    if anchor_idx is None:
        anchor_idx = max(0, len(lines) - fallback_lines)

    # Imponer un límite máximo a la ventana
    if (len(lines) - anchor_idx) > max_window_lines:
        anchor_idx = len(lines) - max_window_lines

    head   = "".join(lines[:anchor_idx])
    window = "".join(lines[anchor_idx:])
    tail   = ""
    return head, window, tail


def detect_base_indent(text: str) -> int:
    """Indentación (en espacios) de la primera línea no vacía."""
    for line in text.splitlines():
        if line.strip():
            return len(line) - len(line.lstrip(" "))
    return 0


def fix_llm_indentation(code_str: str, base_indent: int) -> str:
    """
    Red de seguridad: des-indenta completamente el bloque devuelto por el LLM
    con textwrap.dedent para eliminar desalineaciones globales/bloqueadas del LLM,
    y luego re-indenta uniformemente cada línea no vacía con exactamente `base_indent`
    espacios (preservando la indentación relativa limpia).
    """
    import textwrap
    dedented = textwrap.dedent(code_str)
    lines = dedented.splitlines()
    if not lines:
        return code_str

    fixed = []
    for line in lines:
        if not line.strip():
            fixed.append("")
        else:
            fixed.append(" " * base_indent + line)

    result = "\n".join(fixed)
    if code_str.endswith("\n"):
        result += "\n"
    return result


def auto_fix_window_indentation(
    new_window_raw: str,
    base_indent: int,
    head: str,
    tail: str,
    full_source: str,
    func_name: str,
    max_passes: int = 15,
) -> str:
    """
    Intenta reparar desalineaciones e 'indent jumps' producidos por el LLM dentro de `new_window`.
    Escanea línea por línea y si detecta un salto de indentación no permitido (ej. línea anterior
    no termina en ':', '(', '{', '[', '\\'), desplaza el bloque en conflicto hasta lograr que
    ast.parse del archivo completo sea válido.
    También utiliza el número de línea exacto provisto por el error de SyntaxError para realizar
    correcciones alineadas al bloque anterior.
    """
    new_window = fix_llm_indentation(new_window_raw, base_indent)
    lines = new_window.splitlines()
    head_lines = head.count("\n")

    for pass_num in range(max_passes):
        cur_window = "\n".join(lines) + "\n"
        candidate_block = head + cur_window + tail
        candidate_source = replace_function_block(full_source, func_name, candidate_block)
        
        valid, err = validate_python_syntax_detailed(candidate_source)
        if valid:
            logger.info("Auto-reparación de indentación exitosa en pase %d.", pass_num)
            return cur_window

        # Intentar corrección quirúrgica si el compilador nos da el número de línea
        if err and err.lineno is not None:
            rel_line = err.lineno - head_lines - 1
            if 0 <= rel_line < len(lines):
                line_content = lines[rel_line]
                stripped = line_content.lstrip()
                if stripped:
                    p_line = rel_line - 1
                    while p_line >= 0 and not lines[p_line].strip():
                        p_line -= 1
                    
                    p_indent = base_indent
                    if p_line >= 0:
                        p_line_content = lines[p_line]
                        p_indent = len(p_line_content) - len(p_line_content.lstrip())

                    # Probar candidatos de indentación según el tipo de error
                    candidates_indents = []
                    if "expected an indented block" in str(err):
                        candidates_indents = [p_indent + 4]
                    else:
                        candidates_indents = [p_indent, p_indent - 4, p_indent - 8, p_indent + 4]

                    repaired_this_pass = False
                    for candidate_indent in candidates_indents:
                        if candidate_indent < base_indent:
                            continue
                        
                        test_lines = list(lines)
                        test_lines[rel_line] = " " * candidate_indent + stripped
                        test_window = "\n".join(test_lines) + "\n"
                        test_block = head + test_window + tail
                        test_source = replace_function_block(full_source, func_name, test_block)
                        
                        v, _ = validate_python_syntax_detailed(test_source)
                        if v:
                            logger.info("Auto-reparación AST quirúrgica exitosa en línea %d con indentación %d.", rel_line + 1, candidate_indent)
                            lines = test_lines
                            repaired_this_pass = True
                            break
                    
                    if repaired_this_pass:
                        continue

        # Fallback a heurística de escaneo lineal existente
        repaired = False
        for idx in range(1, len(lines)):
            curr = lines[idx]
            prev = lines[idx - 1]
            if not curr.strip() or not prev.strip():
                continue

            c_indent = len(curr) - len(curr.lstrip(" "))
            p_indent = len(prev) - len(prev.lstrip(" "))

            prev_trimmed = prev.rstrip()
            allowed_increase = prev_trimmed.endswith((":", "(", "{", "[", "\\"))

            if not allowed_increase and c_indent > p_indent:
                shift = c_indent - p_indent
                logger.info(
                    "Detectado salto de indentación inválido en línea %d de la ventana (%d -> %d). Ajustando shift -%d...",
                    idx + 1, p_indent, c_indent, shift,
                )
                for j in range(idx, len(lines)):
                    if lines[j].strip():
                        j_indent = len(lines[j]) - len(lines[j].lstrip(" "))
                        lines[j] = " " * max(0, j_indent - shift) + lines[j].lstrip(" ")
                repaired = True
                break

        if not repaired:
            break

    result = "\n".join(lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


# ---------------------------------------------------------------------------
# Sintaxis
# ---------------------------------------------------------------------------

def validate_python_syntax(code: str) -> tuple[bool, str]:
    """Valida que el código sea Python sintácticamente correcto."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def validate_python_syntax_detailed(code: str) -> tuple[bool, SyntaxError | None]:
    """Valida y retorna la excepción de sintaxis exacta para análisis estructural."""
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, e


# ---------------------------------------------------------------------------
# Backup / Rollback
# ---------------------------------------------------------------------------

def make_backup(filepath: Path) -> Path:
    backup = filepath.with_suffix(BACKUP_SUFFIX)
    shutil.copy2(filepath, backup)
    logger.info("Backup creado: %s", backup)
    return backup


def rollback(filepath: Path, backup: Path) -> None:
    shutil.copy2(backup, filepath)
    logger.warning("Rollback aplicado: %s → %s", backup, filepath)


# ---------------------------------------------------------------------------
# LLM — construcción de prompt y llamada
# ---------------------------------------------------------------------------

def build_prompt(
    window: str,
    test_output: str,
    cycle: int,
    base_indent: int,
    func_name: str,
    target_file: str,
    betweenness: float | None = None,
    cycle_history: str = "",
    current_metric: float = 0.0,
) -> str:
    """
    Construye el prompt de diagnóstico para el LLM.
    IMPORTANTE: solo se muestra `window` (la sección anclada),
    y solo se le pide al modelo que devuelva el reemplazo de ESA ventana.
    Nunca se le pide reconstruir código que no se le mostró.
    Ahora incluye contexto de criticidad graphify, memoria de ciclos anteriores y RAG de Second Brain.
    """
    test_tail = test_output[-1500:].strip()

    lines = [
        "Eres un ingeniero senior de automatización web y Python, especializado en Angular v18 y Selenium.",
        "",
        "CONTEXTO CRÍTICO:",
        "Abajo ves SOLO UN FRAGMENTO del método, no la función completa.",
        "Ese fragmento empieza exactamente en el primer PASO mostrado y llega hasta el final del método.",
        "TODO el código antes de este fragmento ya existe en el archivo y tu NO lo puedes ver ni debes repetir la firma de la función.",
        "",
        "REGLA DE SALIDA (obligatoria):",
        f"- Responde ÚNICAMENTE con el reemplazo de este fragmento (misma indentación base: {base_indent} espacios).",
        "- NO incluyas la firma `def ...` de la función.",
        "- NO repitas código de pasos anteriores al fragmento mostrado.",
        "- Conserva TODOS los yields SSE existentes dentro del fragmento.",
        "- El bloque try/except (si aparece en el fragmento) debe quedar con indentación consistente y válida.",
        "",
        f"Tu tarea es mejorar el fragmento de `{func_name}` en `{target_file}`.",
        "",
    ]

    # Contexto de criticidad graphify
    if betweenness is not None:
        lines += [
            "=== CONTEXTO DE CRITICIDAD (Graphify) ===",
            f"Criticidad en el grafo del sistema: {betweenness:.4f} betweenness centrality",
            "(0.0 = nodo periférico, 1.0 = nodo más crítico del codebase — este módulo es un puente entre comunidades de código).",
            "Prioriza estabilidad y compatibilidad por sobre refactorizaciones agresivas.",
            "",
        ]

    # Contexto de conocimiento del Second Brain (RAG)
    try:
        sb_context = get_second_brain_context(target_file, func_name)
        if sb_context:
            lines += [sb_context, ""]
    except Exception as exc:
        logger.warning("Error recuperando contexto de Second Brain: %s", exc)

    # Directiva de Micro-Refactorización si las pruebas ya pasan al 100%
    if current_metric >= 1.0:
        lines += [
            "=== DIRECTIVA DE MICRO-REFACTORIZACIÓN DE ALTO RENDIMIENTO ===",
            "Las pruebas actuales ya están pasando al 100%. Tu objetivo es REFACTORIZAR ACTIVAMENTE:",
            "- Optimiza tiempos de reintento HTTP/DOM reduciendo sleep redundantes.",
            "- Agrega manejo de excepciones defensivo específico en lugar de except genéricos.",
            "- Elimina variables temporales redundantes y mejora la legibilidad del código.",
            "IMPORTANTE: Genera una versión refactorizada superior. No devuelvas exactamente el mismo código.",
            "",
        ]

    # Historial de ciclos anteriores
    if cycle_history:
        lines += [
            "=== HISTORIAL RSI (últimos ciclos del mismo objetivo) ===",
            cycle_history,
            "IMPORTANTE: Si en el historial ves errores de indentación repetidos, presta especial atención",
            "a mantener indentación CONSISTENTE (usa exactamente 4 espacios de incremento por nivel).",
            "",
        ]

    lines += [
        "=== CONTEXTO DEL PORTAL ===",
        ANGULAR_PORTAL_CONTEXT,
        f"=== CICLO RSI: {cycle} ===",
        "=== FRAGMENTO A REESCRIBIR (esto es TODO lo que puedes editar) ===",
        "```python",
        window,
        "```",
        "",
        "=== EJEMPLO DE FORMATO DE RESPUESTA ===",
        f"Si la indentación base es de {base_indent} espacios, tu respuesta DEBE tener esta indentación base:",
        "```python",
        " " * base_indent + "try:",
        " " * base_indent + "    # Acción o paso mejorado...",
        " " * base_indent + "except Exception as exc:",
        " " * base_indent + "    logger.error('Error: %s', exc)",
        "```",
        "",
        "=== OUTPUT DE LOS TESTS ===",
        "```",
        test_tail,
        "```",
        "",
        "=== INSTRUCCIONES ===",
        "1. Analiza qué parte de la lógica puede mejorarse:",
        "   - Tiempos de espera (WebDriverWait, time.sleep)",
        "   - Selectores CSS/XPath para botones de descarga Angular v18",
        "   - Manejo de StaleElementReferenceException",
        "   - Robustez del fallback CDP (network log)",
        "   - Claridad y corrección del prompt de extracción (si aplica)",
        "2. Responde ÚNICAMENTE con el código Python del fragmento reemplazado, dentro de un bloque ```python ... ```.",
        f"   La primera línea de tu respuesta de código Python DEBE empezar con exactamente {base_indent} espacios de indentación.",
        "   No agregues explicaciones en texto plano fuera del bloque de código."
    ]
    return "\n".join(lines)


def call_llama_server(prompt: str) -> str:
    """Llama al llama-server con el prompt de optimización."""
    url = f"{LLAMA_URL}/completion"
    formatted_prompt = f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    payload = {
        "prompt": formatted_prompt,
        "n_predict": 1400,
        "temperature": 0.2,
        "stop": ["<end_of_turn>", "<eos>"]
    }

    logger.info("Enviando prompt al llama-server (%s)...", LLAMA_MODEL)

    try:
        r = httpx.post(
            f"{LLAMA_URL}/completion",
            json=payload,
            timeout=900.0,
        )
        if r.status_code == 200:
            content = r.json().get("content", "").strip()
            logger.info("Respuesta recibida (%d chars).", len(content))
            return content
        logger.error("Error HTTP %d desde llama-server: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.error("Error crítico llamando al llama-server: %s", exc)

    return ""


def extract_python_block(raw: str) -> str:
    """
    Extrae el primer bloque ```python ... ``` de la respuesta del LLM.
    En modo ventana quirúrgica el LLM solo devuelve un fragmento interno,
    no la función completa.
    """
    m = re.search(r"```python\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m2 = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
    if m2:
        return m2.group(1).strip()

    # Fallback: bloque abierto pero sin cerrar (respuesta cortada por n_predict).
    # Tomamos desde el primer ```python (o ```) hasta el final de la respuesta.
    m3 = re.search(r"```python\s*(.*)$", raw, re.DOTALL | re.IGNORECASE)
    if m3:
        return m3.group(1).strip()
    m4 = re.search(r"```\s*(.*)$", raw, re.DOTALL)
    if m4:
        return m4.group(1).strip()

    return ""


# ---------------------------------------------------------------------------
# Motor RSI principal (genérico)
# ---------------------------------------------------------------------------

def run_rsi_stream(
    max_cycles: int = _DEFAULT_MAX_CYCLES,
    dry_run: bool = False,
    target_file: str = _DEFAULT_TARGET_FILE,
    func_name: str = _DEFAULT_FUNC_NAME,
    eval_cmd: str = _DEFAULT_EVAL_CMD,
    eval_metric: str = _DEFAULT_EVAL_METRIC,
    patch_anchors: list[str] | None = None,
    max_window: int = _DEFAULT_MAX_WINDOW,
    delta_threshold: float = 0.02,
    max_stagnant_cycles: int = 2,
):
    """
    Generador que ejecuta los ciclos RSI y emite diccionarios de eventos para SSE.
    Soporta Early Stopping si 2 ciclos consecutivos no superan el delta_threshold (+0.02 por defecto).
    """
    if patch_anchors is None:
        patch_anchors = list(_DEFAULT_PATCH_ANCHORS)

    target_path = Path(target_file)

    yield {
        "status": "start",
        "msg": (
            f"Iniciando Zohar RSI (objetivo: {target_file} → {func_name}, "
            f"ciclos={max_cycles}, dry_run={dry_run}, eval_metric={eval_metric})"
        ),
        "pct": 0,
        "cycle": 0,
        "target_file": target_file,
        "func_name": func_name,
    }

    if not target_path.exists():
        yield {"status": "error", "msg": f"Archivo objetivo no encontrado: {target_file}", "pct": 100}
        return

    # Verificar llama-server
    try:
        r = httpx.get(f"{LLAMA_URL}/health", timeout=3.0)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
    except Exception as exc:
        yield {"status": "error", "msg": f"llama-server no disponible en {LLAMA_URL}: {exc}", "pct": 100}
        return

    # Leer betweenness del grafo graphify
    betweenness = get_graphify_betweenness(func_name)
    if betweenness is not None:
        yield {
            "status": "info",
            "msg": f"Graphify: betweenness de `{func_name}` = {betweenness:.4f}",
            "pct": 2,
        }

    # Estado inicial — evaluación base
    metric_initial, test_output = run_eval(eval_cmd, eval_metric)
    n_passed_display = 0
    m = re.search(r"(\d+) passed", test_output)
    if m:
        n_passed_display = int(m.group(1))

    yield {
        "status": "progress",
        "msg": f"Evaluación inicial: {eval_metric}={metric_initial:.4f}",
        "pct": 10,
        "metric": metric_initial,
    }
    log_jsonl({
        "event":       "start",
        "cycle":       0,
        "target_file": target_file,
        "func_name":   func_name,
        "metric_initial": metric_initial,
        "eval_metric": eval_metric,
    })

    consecutive_no_change = 0
    stagnant_cycles = 0
    current_metric = metric_initial

    for cycle in range(1, max_cycles + 1):
        pct_cycle = 10 + int((cycle - 1) / max_cycles * 80)
        yield {
            "status": "progress",
            "msg": f"─── CICLO RSI {cycle}/{max_cycles} ─── Extrayendo función objetivo...",
            "pct": pct_cycle,
            "cycle": cycle,
        }

        source = target_path.read_text(encoding="utf-8")
        try:
            current_block, _, _ = extract_function_block(source, func_name)
        except ValueError as e:
            yield {"status": "error", "msg": f"No se pudo extraer función: {e}", "pct": 100}
            break

        head, window, tail = extract_patch_window(
            current_block, patch_anchors, max_window_lines=max_window
        )
        base_indent = detect_base_indent(window)

        # Cargar historial de ciclos anteriores para el prompt
        cycle_history = get_cycle_history(target_file, func_name, n=3)

        yield {
            "status": "progress",
            "msg": (
                f"Enviando prompt (ventana de {window.count(chr(10))} líneas de "
                f"{current_block.count(chr(10))} totales) a Gemma 4 E2B en :8083..."
            ),
            "pct": pct_cycle + 5,
            "cycle": cycle,
        }

        prompt = build_prompt(
            window=window,
            test_output=test_output,
            cycle=cycle,
            base_indent=base_indent,
            func_name=func_name,
            target_file=target_file,
            betweenness=betweenness,
            cycle_history=cycle_history,
            current_metric=current_metric,
        )
        llm_response = call_llama_server(prompt)

        if not llm_response:
            yield {"status": "warning", "msg": f"El LLM no retornó respuesta. Saltando ciclo {cycle}.", "pct": pct_cycle + 10}
            log_jsonl({
                "event": "llm_empty", "cycle": cycle,
                "target_file": target_file, "func_name": func_name,
            })
            stagnant_cycles += 1
            if stagnant_cycles >= max_stagnant_cycles:
                yield {
                    "status": "early_stopping",
                    "msg": f"🛑 Early Stopping: {stagnant_cycles} ciclos consecutivos sin respuesta/mejora significativa. Finalizando RSI.",
                    "pct": 95,
                }
                break
            continue

        new_window_raw = extract_python_block(llm_response)
        if not new_window_raw:
            yield {"status": "warning", "msg": f"No se encontró bloque Python en respuesta. Saltando ciclo {cycle}.", "pct": pct_cycle + 10}
            log_jsonl({
                "event": "no_python_block", "cycle": cycle,
                "target_file": target_file, "func_name": func_name,
                "llm_preview": llm_response[:200],
            })
            stagnant_cycles += 1
            if stagnant_cycles >= max_stagnant_cycles:
                yield {
                    "status": "early_stopping",
                    "msg": f"🛑 Early Stopping: {stagnant_cycles} ciclos consecutivos sin bloque Python válido. Finalizando RSI.",
                    "pct": 95,
                }
                break
            continue

        auto_repaired_syntax = False
        new_window = fix_llm_indentation(new_window_raw, base_indent)
        if not new_window.endswith("\n"):
            new_window += "\n"

        candidate_block  = head + new_window + tail
        candidate_source = replace_function_block(source, func_name, candidate_block)
        valid, syntax_err = validate_python_syntax(candidate_source)
        if not valid:
            logger.info("Sintaxis inicial inválida (%s). Intentando auto-reparación de indentación...", syntax_err)
            repaired_window  = auto_fix_window_indentation(
                new_window_raw, base_indent, head, tail, source, func_name
            )
            candidate_block  = head + repaired_window + tail
            candidate_source = replace_function_block(source, func_name, candidate_block)
            valid, syntax_err = validate_python_syntax(candidate_source)
            if valid:
                new_window = repaired_window
                auto_repaired_syntax = True
                logger.info("[AUTO_REPAIRED_SYNTAX] Auto-reparación AST exitosa para ciclo %d.", cycle)

        # Fallback a Gemini Cloud API si la propuesta local sigue siendo inválida
        if not valid:
            gemini_key = os.environ.get("GEMINI_API_KEY")
            if gemini_key:
                yield {
                    "status": "progress",
                    "msg": f"La propuesta local de Gemma 4 es inválida ({syntax_err}). Iniciando fallback a Gemini Cloud...",
                    "pct": pct_cycle + 8,
                    "cycle": cycle,
                }
                logger.info("Iniciando fallback a Gemini Cloud API para el ciclo %d...", cycle)
                try:
                    from core.llm_client import query_gemini_api
                    gemini_prompt = prompt + f"\n\nATENCIÓN: Tu anterior propuesta local falló con el error sintáctico:\n{syntax_err}\nPor favor, genera una versión corregida que compile perfectamente sin errores de indentación."
                    gemini_resp = query_gemini_api(gemini_prompt)
                    
                    if gemini_resp and not gemini_resp.startswith("[LLM Error]"):
                        gemini_window_raw = extract_python_block(gemini_resp)
                        if gemini_window_raw:
                            gemini_window = fix_llm_indentation(gemini_window_raw, base_indent)
                            if not gemini_window.endswith("\n"):
                                gemini_window += "\n"
                            gemini_candidate_block = head + gemini_window + tail
                            gemini_candidate_source = replace_function_block(source, func_name, gemini_candidate_block)
                            
                            gemini_valid, gemini_syntax_err = validate_python_syntax(gemini_candidate_source)
                            if not gemini_valid:
                                # Intentar auto-reparación también sobre el código de Gemini
                                gemini_repaired = auto_fix_window_indentation(
                                    gemini_window_raw, base_indent, head, tail, source, func_name
                                )
                                gemini_candidate_block = head + gemini_repaired + tail
                                gemini_candidate_source = replace_function_block(source, func_name, gemini_candidate_block)
                                gemini_valid, gemini_syntax_err = validate_python_syntax(gemini_candidate_source)
                                if gemini_valid:
                                    gemini_window = gemini_repaired
                            
                            if gemini_valid:
                                new_window = gemini_window
                                candidate_block = gemini_candidate_block
                                candidate_source = gemini_candidate_source
                                valid = True
                                logger.info("[GEMINI_FALLBACK_SUCCESS] Gemini Cloud API generó código válido para el ciclo %d.", cycle)
                            else:
                                syntax_err = gemini_syntax_err
                except Exception as g_exc:
                    logger.error("Error durante el fallback a Gemini Cloud: %s", g_exc)

        # Compute diff preview (para log y mensaje)
        old_set = set(window.splitlines())
        new_set = set(new_window.splitlines())
        added   = [l for l in new_set   if l not in old_set]
        removed = [l for l in old_set if l not in new_set]
        diff_preview = "\n".join(f"+ {l}" for l in added[:5])

        if not valid:
            with open(f"debug_failed_c{cycle}.py", "w", encoding="utf-8") as dbg_f:
                dbg_f.write(candidate_block)
            yield {
                "status": "warning",
                "msg": f"Sintaxis inválida en propuesta: {syntax_err}",
                "pct": pct_cycle + 10,
            }
            log_jsonl({
                "event":              "syntax_error",
                "cycle":              cycle,
                "target_file":        target_file,
                "func_name":          func_name,
                "error":              syntax_err,
                "metric_before":      current_metric,
                "metric_after":       None,
                "window_diff_preview": diff_preview[:300],
            })
            stagnant_cycles += 1
            if stagnant_cycles >= max_stagnant_cycles:
                yield {
                    "status": "early_stopping",
                    "msg": f"🛑 Early Stopping: {stagnant_cycles} ciclos consecutivos con sintaxis inválida. Finalizando RSI.",
                    "pct": 95,
                }
                break
            continue

        if new_window.strip() == window.strip():
            yield {
                "status": "info",
                "msg": f"Ciclo {cycle}: El LLM propuso el mismo código. Sin cambios.",
                "pct": pct_cycle + 15,
            }
            consecutive_no_change += 1
            stagnant_cycles += 1
            log_jsonl({
                "event":       "no_change",
                "cycle":       cycle,
                "target_file": target_file,
                "func_name":   func_name,
                "metric_before": current_metric,
                "metric_after":  current_metric,
            })
            if consecutive_no_change >= 2 or stagnant_cycles >= max_stagnant_cycles:
                yield {"status": "early_stopping", "msg": f"🛑 Early Stopping: Convergencia detectada ({stagnant_cycles} ciclos sin cambios). Finalizando RSI.", "pct": 95}
                break
            continue
        consecutive_no_change = 0

        diff_summary = {
            "added_lines":   len(added),
            "removed_lines": len(removed),
            "preview_added": added[:3],
        }

        yield {
            "status": "diff",
            "msg": f"Diff propuesto ciclo {cycle}: +{len(added)} / -{len(removed)} líneas",
            "diff": diff_summary,
            "pct": pct_cycle + 12,
        }

        if dry_run:
            yield {
                "status": "dry_run",
                "msg": f"[DRY-RUN] Parche generado exitosamente para ciclo {cycle} (no aplicado)",
                "pct": pct_cycle + 20,
                "preview": new_window[:300],
            }
            log_jsonl({
                "event":              "dry_run",
                "cycle":              cycle,
                "target_file":        target_file,
                "func_name":          func_name,
                "diff":               diff_summary,
                "metric_before":      current_metric,
                "window_diff_preview": diff_preview[:300],
            })
            continue

        backup_path = make_backup(target_path)

        try:
            target_path.write_text(candidate_source, encoding="utf-8")
        except Exception as e:
            rollback(target_path, backup_path)
            yield {"status": "error", "msg": f"Error aplicando parche: {e}. Rollback realizado.", "pct": pct_cycle + 20}
            log_jsonl({
                "event": "patch_apply_error", "cycle": cycle,
                "target_file": target_file, "func_name": func_name, "error": str(e),
            })
            stagnant_cycles += 1
            if stagnant_cycles >= max_stagnant_cycles:
                yield {"status": "early_stopping", "msg": f"🛑 Early Stopping: Error de aplicación de parche repetido. Finalizando RSI.", "pct": 95}
                break
            continue

        full_source_after = target_path.read_text(encoding="utf-8")
        valid_full, syntax_err_full = validate_python_syntax(full_source_after)
        if not valid_full:
            rollback(target_path, backup_path)
            yield {
                "status": "error",
                "msg": f"Sintaxis global inválida tras parche: {syntax_err_full}. Rollback realizado.",
                "pct": pct_cycle + 20,
            }
            log_jsonl({
                "event":       "file_syntax_error",
                "cycle":       cycle,
                "target_file": target_file,
                "func_name":   func_name,
                "error":       syntax_err_full,
                "metric_before": current_metric,
                "metric_after":  None,
            })
            stagnant_cycles += 1
            if stagnant_cycles >= max_stagnant_cycles:
                yield {"status": "early_stopping", "msg": f"🛑 Early Stopping: Sintaxis global inválida ({stagnant_cycles} fallos). Finalizando RSI.", "pct": 95}
                break
            continue

        yield {"status": "progress", "msg": f"Re-evaluando con `{eval_metric}` para ciclo {cycle}...", "pct": pct_cycle + 18}
        new_metric, test_output_after = run_eval(eval_cmd, eval_metric)

        delta = new_metric - current_metric
        if delta >= delta_threshold:
            stagnant_cycles = 0  # Restablecer si superó el umbral delta!
            yield {
                "status": "cycle_success",
                "msg": (
                    f"[PASS] Ciclo {cycle} EXITOSO — {eval_metric}: "
                    f"{current_metric:.4f} → {new_metric:.4f} ({delta:+.4f} ≥ +{delta_threshold:.2f})"
                ),
                "metric": new_metric,
                "pct": pct_cycle + 20,
            }
            log_jsonl({
                "event":              "cycle_success",
                "cycle":              cycle,
                "target_file":        target_file,
                "func_name":          func_name,
                "metric_before":      current_metric,
                "metric_after":       new_metric,
                "diff":               diff_summary,
                "window_diff_preview": diff_preview[:300],
            })
            try:
                save_rsi_learning(target_file, func_name, cycle, current_metric, new_metric, diff_preview, auto_repaired=auto_repaired_syntax)
            except Exception as exc:
                logger.warning("Error guardando aprendizaje en Second Brain: %s", exc)
            test_output   = test_output_after
            current_metric = new_metric
        else:
            stagnant_cycles += 1
            if new_metric > current_metric:
                yield {
                    "status": "cycle_warning",
                    "msg": (
                        f"⚠️ Ciclo {cycle} MEJORA MENOR AL UMBRAL (+{delta:.4f} < +{delta_threshold:.2f}). "
                        f"Métrica: {current_metric:.4f} → {new_metric:.4f}. ({stagnant_cycles}/{max_stagnant_cycles} ciclos estancados)"
                    ),
                    "metric": new_metric,
                    "pct": pct_cycle + 20,
                }
                test_output = test_output_after
                current_metric = new_metric
            else:
                rollback(target_path, backup_path)
                yield {
                    "status": "cycle_rollback",
                    "msg": (
                        f"❌ Ciclo {cycle} FALLIDO — {eval_metric}: "
                        f"{current_metric:.4f} → {new_metric:.4f} ({delta:+.4f}). Rollback aplicado. ({stagnant_cycles}/{max_stagnant_cycles} estancados)"
                    ),
                    "metric": new_metric,
                    "pct": pct_cycle + 20,
                }
                log_jsonl({
                    "event":              "cycle_rollback",
                    "cycle":              cycle,
                    "target_file":        target_file,
                    "func_name":          func_name,
                    "metric_before":      current_metric,
                    "metric_after":       new_metric,
                    "diff":               diff_summary,
                    "window_diff_preview": diff_preview[:300],
                })
                test_output = test_output_after + "\n[ROLLBACK EJECUTADO]\n" + test_output

        if stagnant_cycles >= max_stagnant_cycles:
            yield {
                "status": "early_stopping",
                "msg": f"🛑 Early Stopping: {stagnant_cycles} ciclos consecutivos sin mejora significativa (≥+{delta_threshold:.2f}). Finalizando RSI.",
                "pct": 95,
            }
            log_jsonl({
                "event": "early_stopping",
                "target_file": target_file,
                "func_name": func_name,
                "stagnant_cycles": stagnant_cycles,
                "delta_threshold": delta_threshold
            })
            break

    yield {
        "status": "complete",
        "msg": (
            f"RSI completado. {eval_metric} final: {current_metric:.4f} "
            f"(inicial: {metric_initial:.4f}, delta: {current_metric - metric_initial:+.4f})"
        ),
        "final_metric": current_metric,
        "initial_metric": metric_initial,
        "pct": 100,
    }
    log_jsonl({
        "event":         "rsi_done",
        "target_file":   target_file,
        "func_name":     func_name,
        "metric_initial": metric_initial,
        "metric_final":   current_metric,
    })


def run_rsi(
    max_cycles: int = _DEFAULT_MAX_CYCLES,
    dry_run: bool = False,
    target_file: str = _DEFAULT_TARGET_FILE,
    func_name: str = _DEFAULT_FUNC_NAME,
    eval_cmd: str = _DEFAULT_EVAL_CMD,
    eval_metric: str = _DEFAULT_EVAL_METRIC,
    patch_anchors: list[str] | None = None,
    max_window: int = _DEFAULT_MAX_WINDOW,
    delta_threshold: float = 0.02,
    max_stagnant_cycles: int = 2,
) -> None:
    logger.info("=" * 60)
    logger.info("Iniciando Zohar RSI — Motor de Auto-Mejora Recursiva")
    logger.info("Objetivo: %s → %s", target_file, func_name)
    logger.info("Ciclos: %d | Dry-run: %s | eval_metric: %s | delta: %.2f | max_stagnant: %d",
                max_cycles, dry_run, eval_metric, delta_threshold, max_stagnant_cycles)
    logger.info("=" * 60)

    for event in run_rsi_stream(
        max_cycles=max_cycles,
        dry_run=dry_run,
        target_file=target_file,
        func_name=func_name,
        eval_cmd=eval_cmd,
        eval_metric=eval_metric,
        patch_anchors=patch_anchors,
        max_window=max_window,
        delta_threshold=delta_threshold,
        max_stagnant_cycles=max_stagnant_cycles,
    ):
        level = event.get("status", "info")
        msg   = event.get("msg", "")
        if level in ("error", "cycle_rollback", "warning"):
            logger.warning("[%s] %s", level.upper(), msg)
        else:
            logger.info("[%s] %s", level.upper(), msg)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Zohar v4 — Motor de Auto-Mejora Recursiva (RSI) — Multi-Objetivo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Modo clásico con Early Stopping por defecto
  ./venv/bin/python auto_improver.py --cycles 5 --delta-threshold 0.02 --max-stagnant-cycles 2 --dry-run
""",
    )
    parser.add_argument(
        "--cycles", "-c",
        type=int,
        default=_DEFAULT_MAX_CYCLES,
        help=f"Número de ciclos RSI a ejecutar (default: {_DEFAULT_MAX_CYCLES})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera el parche propuesto pero NO lo aplica al archivo",
    )
    parser.add_argument(
        "--target-file",
        type=str,
        default=_DEFAULT_TARGET_FILE,
        help=f"Archivo Python objetivo del RSI (default: {_DEFAULT_TARGET_FILE})",
    )
    parser.add_argument(
        "--func-name",
        type=str,
        default=_DEFAULT_FUNC_NAME,
        help=f"Nombre de la función a optimizar (default: {_DEFAULT_FUNC_NAME})",
    )
    parser.add_argument(
        "--eval-cmd",
        type=str,
        default=_DEFAULT_EVAL_CMD,
        help="Comando shell de evaluación (default: pytest suite)",
    )
    parser.add_argument(
        "--eval-metric",
        type=str,
        default=_DEFAULT_EVAL_METRIC,
        choices=["pytest_pass_rate", "score_float", "exit_code"],
        help="Métrica a extraer del output de eval-cmd (default: pytest_pass_rate)",
    )
    parser.add_argument(
        "--patch-anchors",
        type=str,
        default=",".join(_DEFAULT_PATCH_ANCHORS),
        help="Anclas de ventana quirúrgica, separadas por coma (default: 'PASO 5,PASO 6,PASO 4')",
    )
    parser.add_argument(
        "--max-window",
        type=int,
        default=_DEFAULT_MAX_WINDOW,
        help=f"Máximo de líneas en la ventana quirúrgica (default: {_DEFAULT_MAX_WINDOW})",
    )
    parser.add_argument(
        "--delta-threshold",
        type=float,
        default=0.02,
        help="Umbral mínimo de mejora en la métrica (default: 0.02)",
    )
    parser.add_argument(
        "--max-stagnant-cycles",
        type=int,
        default=2,
        help="Máximo número de ciclos sin mejora significativa antes de Early Stopping (default: 2)",
    )

    args = parser.parse_args()
    anchors = [a.strip() for a in args.patch_anchors.split(",") if a.strip()]

    run_rsi(
        max_cycles=args.cycles,
        dry_run=args.dry_run,
        target_file=args.target_file,
        func_name=args.func_name,
        eval_cmd=args.eval_cmd,
        eval_metric=args.eval_metric,
        patch_anchors=anchors,
        max_window=args.max_window,
        delta_threshold=args.delta_threshold,
        max_stagnant_cycles=args.max_stagnant_cycles,
    )


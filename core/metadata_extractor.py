"""
core/metadata_extractor.py
==========================

Extractor de METADATA ESTRUCTURADA para el Second Brain de Zohar v4.

Paso NUEVO y SEPARADO de infer.py (inferencia de viabilidad). Este módulo
NO hace inferencia de veredicto/score: solo localiza y (posteriormente, en
el Bloque 2) extrae metadatos fácticos del MIA:
  clave, promovente, municipio, estado, localidad, descripción_del_proyecto.

Este archivo (Bloque 1) contiene SOLO:
  - localización determinista de snippets (sin LLM)
  - el esquema propuesto de tabla
  - construcción del prompt (string, no se llama)
  - validación post-extracción (reusa patrón de audit_report.json)

El LLM NO se invoca aquí. La extracción real queda para el Bloque 2 tras
aprobación explícita.
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from datetime import datetime, timezone

from core.text_utils import build_targeted_snippet, _ESTADOS_MX

# ── Regex de clave SINAT (prefijo de 13c, corregido en commit c38ef22) ───────
CLAVE_RE = re.compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")

# Lista cerrada de 32 entidades federativas (de text_utils._ESTADOS_MX)
ESTADOS_VALIDOS = set(e.upper() for e in _ESTADOS_MX)

# ── Configuración de recorte dinámico (Fase 1 / Bloque 2) ──────────────────────
# Los topes de snippet ya NO son constantes fijas de 4000/6000: se derivan del
# contexto real del modelo (-c / n_ctx) consultando /props del llama-server, con
# un margen para la respuesta. Ver TECHNICAL_DEBT.md (patrón de índice vs cuerpo).
CHARS_PER_TOKEN = 4
MARGEN_RESPUESTA_TOKENS = 1000   # margen reservado para que el LLM genere el JSON
DEFAULT_N_CTX = 4096             # fallback si /props no responde
LLAMA_SERVER_URL = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083")
# Proporción del presupuesto destinada a cada snippet
DG_FRAC = 0.40
DESC_FRAC = 0.60

def get_model_n_ctx(url: str = LLAMA_SERVER_URL, timeout: float = 3.0) -> int:
    """Lee el contexto real (-c / n_ctx) del llama-server vía /props.
    Fallback a DEFAULT_N_CTX si el server no responde (p. ej. congelado)."""
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{url.rstrip('/')}/props")
        if r.status_code == 200:
            d = r.json()
            n_ctx = d.get("default_generation_settings", {}).get("n_ctx")
            if isinstance(n_ctx, int) and n_ctx > 0:
                return n_ctx
    except Exception:
        pass
    return DEFAULT_N_CTX

def compute_snippet_budget(n_ctx: int | None = None) -> int:
    """Presupuesto total de caracteres para los snippets, derivado de n_ctx."""
    if n_ctx is None:
        n_ctx = get_model_n_ctx()
    disponibles = max(512, n_ctx - MARGEN_RESPUESTA_TOKENS)
    return max(2000, disponibles * CHARS_PER_TOKEN)

# Patrones que distinguen una ENTRADA DE ÍNDICE/TABLA DE CONTENIDO de un cuerpo real.
# Evidencia (audit_headers.py sobre 12GE2026V0006, 02BC2022E0016, 04CA2026E0011):
#  - la PRIMERA aparición de "DATOS GENERALES" es siempre el índice.
#  - pymupdf4llm es inconsistente: a veces el cuerpo lleva '#'/'##', a veces es
#    texto plano ("I. DATOS GENERALES..."). Pero el índice SIEMPRE tiene puntos
#    suspensivos (.....) o es línea de tabla '|...|'. El cuerpo real NUNCA los tiene.
_INDEX_DOTS = re.compile(r"\.{3,}")
_INDEX_TABLE = re.compile(r"^\s*\|")
_MD_HEADER = re.compile(r"^\s*(#{1,6}\s|-\s+#{1,6}\s)")
# (a) número de página al final: puntos (aunque sean pocos) seguidos de dígito(s)
#     al final de la ventana, o un dígito final cuando ya hubo puntos antes.
_INDEX_DOTS_PAGE = re.compile(r"\.\s*\d+\s*$")
_INDEX_PAGE_TAIL = re.compile(r"\d+\s*$")
# (b) tag HTML de índice (subrayado de TOC visto en 04CA2026E0011)
_INDEX_HTML_U = re.compile(r"</?u>", re.IGNORECASE)
# (c) marcador de lista al inicio de línea (bullet o numeración)
_LIST_MARKER = re.compile(r"^\s*([-*]\s+|\d+\.\s+)")

def _is_index_entry(line: str) -> bool:
    """True si la línea/ventana corresponde a una entrada de índice/TOC.

    Criterio negativo (dirigido por evidencia; ver TECHNICAL_DEBT.md): el cuerpo
    real NUNCA lleva puntos de relleno + número de página ni tags <u> de TOC. Se
    detecta:
      base) puntos suspensivos '.....' o fila de tabla '|...'
      (a)   puntos + número de página al final ('.... 4'), o dígito final cuando
            ya hay puntos suspensivos en la ventana
      (b)   tag HTML <u>/<u> (subrayado de TOC)
      (c)   bullet/numeración al inicio SOLO cuando la línea también tiene el
            patrón completo de índice (puntos suspensivos + número de página)
    """
    if _INDEX_DOTS.search(line) or _INDEX_TABLE.match(line):
        return True
    # (a) número de página al final de la ventana
    if _INDEX_DOTS_PAGE.search(line):
        return True
    if "." in line and _INDEX_PAGE_TAIL.search(line) and _INDEX_DOTS.search(line):
        return True
    # (b) tag HTML de índice
    if _INDEX_HTML_U.search(line):
        return True
    # (c) bullet/numeración de lista COMBINADO con patrón de índice completo
    if _LIST_MARKER.match(line) and _INDEX_DOTS.search(line) and _INDEX_PAGE_TAIL.search(line):
        return True
    return False

def _find_section_span(text: str, pattern: re.Pattern, window: int) -> tuple[int, int] | None:
    """Devuelve (start, end) del CUERPO REAL de la sección, no la entrada de índice.

    Lógica (dirigida por evidencia empírica, ver TECHNICAL_DEBT.md):
      1. Reunir todas las coincidencias del patrón.
      2. Descartar las que son entrada de índice (puntos suspensivos / tabla).
      3. De las restantes, preferir la PRIMERA con marcador markdown (#/##); si
         ninguna lo tiene, usar la primera restante (ya no es índice).
      4. Fallback: si todas eran índice, usar la ÚLTIMA aparición.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return None

    cuerpo_cands = []   # (start, tiene_md_header)
    for m in matches:
        s = m.start()
        # Bug 2 fix: construir una ventana que incluya la LÍNEA COMPLETA que
        # contiene el match MÁS ~120 chars de lookahead, para no truncar antes
        # de los puntos suspensivos + número de página del índice (visto en
        # 02BC2022E0016 pos~930, donde el mapeo previo cortaba la ventana).
        line_start = text.rfind("\n", 0, s) + 1  # 0 si no hay salto previo
        nl = text.find("\n", s)
        line_end = nl if nl != -1 else len(text)
        # extender lookahead más allá del fin de línea para capturar '.... N'
        window_end = min(len(text), max(line_end, s + 120))
        ventana = text[line_start:window_end]
        if _is_index_entry(ventana):
            continue
        cuerpo_cands.append((s, bool(_MD_HEADER.match(ventana))))

    if cuerpo_cands:
        # preferir la primera con marcador md
        with_md = [c for c in cuerpo_cands if c[1]]
        pick = (with_md or cuerpo_cands)[0][0]
    else:
        # todas eran indice: usar la ultima aparicion (fallback)
        pick = matches[-1].start()

    return (max(0, pick - 200), min(len(text), pick + window))


# ── Secciones objetivo para localizar snippets ────────────────────────────────
# Datos generales / ficha técnica -> clave, promovente, municipio, estado, localidad
# NOTA: 'ficha tecnica' se restringe a la del PROYECTO; se excluye 'ficha tecnica
# de la medida' (medidas mitigatorias, cap. IV) que es un falso positivo frecuente.
_SECCION_DATOS = re.compile(
    r"(1\s+DATOS\s+GENERALES|datos\s+generales\s+del\s+proyecto|"
    r"ficha\s+t[eé]cnica\s+del\s+proyecto|identificaci[oó]n\s+del\s+proyecto|"
    r"del\s+promovente\s+y\s+del\s+responsable)",
    re.IGNORECASE,
)
# Descripción del proyecto
_SECCION_DESC = re.compile(
    r"(descripci[oó]n\s+de\s+las\s+obras\s+o\s+actividades|"
    r"descripci[oó]n\s+del\s+proyecto|"
    r"cap[ií]tulo\s+ii|ii\.\s*\d|"
    r"ii\.\s*descripci)",
    re.IGNORECASE,
)


def locate_metadata_snippets(text: str, n_ctx: int | None = None) -> dict[str, str]:
    """
    Localiza (sin LLM) los tramos de texto relevantes para cada campo.

    Devuelve un dict con:
      'datos_generales': snippet del CUERPO REAL de la sección 1 (promovente,
                         municipio, estado, localidad, clave). NO la entrada
                         de índice/tabla de contenido.
      'descripcion':     snippet del CUERPO REAL de la sección de descripción.
      'prefijo':         primeras ~2000 chars (fallback general).

    Los tamaños de snippet se derivan del contexto real del modelo (n_ctx vía
    /props) con un margen para la respuesta; no son topes fijos. Ver
    TECHNICAL_DEBT.md (patrón índice vs cuerpo y topes dinámicos).

    NO extrae valores; solo entrega el contexto crudo para que el LLM
    (Bloque 2) lo procese.
    """
    if not text:
        return {"datos_generales": "", "descripcion": "", "prefijo": ""}

    out = {"datos_generales": "", "descripcion": "", "prefijo": text[:2000]}

    # Presupuesto dinámico derivado de n_ctx
    budget = compute_snippet_budget(n_ctx)
    win_dg = max(600, int(budget * DG_FRAC))
    win_desc = max(600, int(budget * DESC_FRAC))

    # 1) Sección DATOS GENERALES -> cuerpo real (no índice)
    span = _find_section_span(text, _SECCION_DATOS, window=win_dg)
    if span:
        out["datos_generales"] = text[span[0]:span[1]]

    # 2) Sección DESCRIPCIÓN -> cuerpo real (no índice)
    span2 = _find_section_span(text, _SECCION_DESC, window=win_desc)
    if span2:
        out["descripcion"] = text[span2[0]:span2[1]]
    else:
        out["descripcion"] = build_targeted_snippet(
            text, prefix_chars=win_desc // 2, window_chars=300, max_total_chars=win_desc
        )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Esquema propuesto de tabla (punto 2) — se crea en el Bloque 2 con sqlite3
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata_proyecto (
    clave                  TEXT PRIMARY KEY,
    promovente             TEXT,
    municipio              TEXT,
    estado                 TEXT,
    localidad              TEXT,
    descripcion_proyecto   TEXT,
    confianza_extraccion  TEXT CHECK (confianza_extraccion IN ('alta','media','baja')),
    campos_faltantes       TEXT,           -- JSON array de nombres de campo
    requiere_revision      INTEGER DEFAULT 0,
    snippet_fuente         TEXT,
    fecha_extraccion       TEXT,           -- ISO 8601
    version_prompt         TEXT
);
"""

CAMPOS_EXTRAIBLES = ["clave", "promovente", "municipio", "estado", "localidad", "descripcion_proyecto"]
VERSION_PROMPT = "v1-metadata-estructurada"


def build_extraction_prompt(snippets: dict[str, str]) -> str:
    """
    Construye (sin llamar) el prompt de extracción de UNA sola llamada al LLM.
    Devuelve el string del prompt listo para enviar.
    """
    prompt = f"""Eres un extractor de metadatos de Manifestaciones de Impacto Ambiental (MIA) mexicanas.

Extrae SOLO los siguientes campos del texto proporcionado. REGLA ESTRICTA: extrae
únicamente lo que está literalmente en el texto. NUNCA infieras, completes ni uses
conocimiento externo. Si un campo no aparece en el texto, devuélvelo como null y
agrégalo al arreglo campos_faltantes.

Campos:
- clave: clave SINAT de 13 caracteres (formato NNLLNNNNLNNNN, ej. 04CA2026E0011)
- promovente: persona física/moral que promueve el proyecto
- municipio: municipio donde se ubica el proyecto
- estado: entidad federativa (una de 32, ej. Campeche, Sonora)
- localidad: localidad/asentamiento específico (puede ser null si no aparece)
- descripcion_proyecto: 2 a 4 oraciones que describan el proyecto (resumidas del texto)

Responde ÚNICAMENTE con un JSON válido y estricto, sin markdown, sin texto extra:
{{
  "clave": <string|null>,
  "promovente": <string|null>,
  "municipio": <string|null>,
  "estado": <string|null>,
  "localidad": <string|null>,
  "descripcion_proyecto": <string|null>,
  "confianza_extraccion": <"alta"|"media"|"baja">,
  "campos_faltantes": [<string>, ...]
}}

=== SECCIÓN DATOS GENERALES ===
{snippets.get('datos_generales', '')[:4000]}

=== SECCIÓN DESCRIPCIÓN ===
{snippets.get('descripcion', '')[:6000]}
"""
    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Validación post-extracción (punto 5) — reusa patrón de audit_report.json
# ─────────────────────────────────────────────────────────────────────────────

def validate_extraction(clave: str, data: dict) -> dict:
    """
    Valida un dict extraído por el LLM. Devuelve:
      {
        "ok": bool,                 # False => no se escribe a la tabla
        "nivel": "OK" | "CRITICAL",
        "alertas": [ {campo, tipo_error, nivel, mensaje}, ... ],
        "requiere_revision": bool,
        "campos_faltantes": [str],
      }
    """
    alertas = []
    campos_faltantes = list(data.get("campos_faltantes") or [])

    # 1) clave debe matchear regex de prefijo
    clave_val = (data.get("clave") or "").strip().upper()
    if not CLAVE_RE.match(clave_val):
        alertas.append({
            "campo": "clave",
            "tipo_error": "Clave inválida",
            "nivel": "CRITICAL",
            "mensaje": f"La clave '{clave_val}' no matchea el patrón SINAT NNLLNNNNLNNNN.",
        })

    # 2) estado en lista cerrada de 32 entidades
    estado_val = (data.get("estado") or "").strip().upper()
    if estado_val and estado_val not in ESTADOS_VALIDOS:
        alertas.append({
            "campo": "estado",
            "tipo_error": "Estado fuera de lista cerrada",
            "nivel": "CRITICAL",
            "mensaje": f"El estado '{estado_val}' no está en la lista de 32 entidades federativas.",
        })
        if "estado" not in campos_faltantes:
            campos_faltantes.append("estado")

    # 3) confianza baja o 3+ campos faltantes -> requiere_revision
    conf = (data.get("confianza_extraccion") or "baja").lower()
    n_faltantes = len([c for c in campos_faltantes if c in CAMPOS_EXTRAIBLES])
    requiere_revision = (conf == "baja") or (n_faltantes >= 3)

    nivel = "CRITICAL" if any(a["nivel"] == "CRITICAL" for a in alertas) else "OK"
    ok = nivel != "CRITICAL"

    return {
        "ok": ok,
        "nivel": nivel,
        "alertas": alertas,
        "requiere_revision": requiere_revision,
        "campos_faltantes": campos_faltantes,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Capa de storage SQLite (decisión del usuario: SQLite local, NO PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "metadata_proyecto.db"


def get_connection(db_path: Path | None = None):
    """Abre (y crea si falta) la conexión SQLite a la base de metadata."""
    import sqlite3
    db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Crea la tabla metadata_proyecto si no existe."""
    conn = get_connection(db_path)
    try:
        conn.execute(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def upsert_metadata(clave: str, data: dict, snippet_fuente: str = "",
                    version_prompt: str = VERSION_PROMPT,
                    db_path: Path | None = None) -> dict:
    """
    Escribe (INSERT OR REPLACE) un registro de metadata SI pasa la validación.

    Reglas (punto 5):
      - Si validate_extraction() -> ok=False (alerta CRITICAL: clave inválida
        o estado fuera de lista cerrada) NO se escribe y se devuelve
        {'escrito': False, 'razon': ...}.
      - Si requiere_revision=True, se escribe pero con requiere_revision=1.

    Devuelve:
      {'escrito': bool, 'requiere_revision': bool, 'validacion': <dict>,
       'razon': <str|None>}
    """
    val = validate_extraction(clave, data)
    if not val["ok"]:
        return {
            "escrito": False,
            "requiere_revision": val["requiere_revision"],
            "validacion": val,
            "razon": "; ".join(a["mensaje"] for a in val["alertas"]),
        }

    conf = (data.get("confianza_extraccion") or "baja").lower()
    campos_falt = json.dumps(data.get("campos_faltantes") or val["campos_faltantes"],
                             ensure_ascii=False)

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO metadata_proyecto (
                clave, promovente, municipio, estado, localidad,
                descripcion_proyecto, confianza_extraccion, campos_faltantes,
                requiere_revision, snippet_fuente, fecha_extraccion, version_prompt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (data.get("clave") or clave).strip().upper(),
                data.get("promovente"),
                data.get("municipio"),
                data.get("estado"),
                data.get("localidad"),
                data.get("descripcion_proyecto"),
                conf,
                campos_falt,
                1 if val["requiere_revision"] else 0,
                snippet_fuente[:4000],
                now_iso(),
                version_prompt,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "escrito": True,
        "requiere_revision": val["requiere_revision"],
        "validacion": val,
        "razon": None,
    }


def get_metadata(clave: str, db_path: Path | None = None) -> dict | None:
    """Lee un registro por clave. Devuelve dict o None."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM metadata_proyecto WHERE clave = ?", (clave.upper(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_rows(db_path: Path | None = None) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM metadata_proyecto").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


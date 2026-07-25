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
from pathlib import Path
from datetime import datetime, timezone

from core.text_utils import build_targeted_snippet, _ESTADOS_MX

# ── Regex de clave SINAT (prefijo de 13c, corregido en commit c38ef22) ───────
CLAVE_RE = re.compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")

# Lista cerrada de 32 entidades federativas (de text_utils._ESTADOS_MX)
ESTADOS_VALIDOS = set(e.upper() for e in _ESTADOS_MX)

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


def locate_metadata_snippets(text: str) -> dict[str, str]:
    """
    Localiza (sin LLM) los tramos de texto relevantes para cada campo.

    Devuelve un dict con:
      'datos_generales': snippet alrededor de la sección 1 (promovente,
                         municipio, estado, localidad, clave).
      'descripcion':     snippet alrededor de la sección de descripción.
      'prefijo':         primeras ~2000 chars (fallback general).

    NO extrae valores; solo entrega el contexto crudo para que el LLM
    (Bloque 2) lo procese.
    """
    if not text:
        return {"datos_generales": "", "descripcion": "", "prefijo": ""}

    out = {"datos_generales": "", "descripcion": "", "prefijo": text[:2000]}

    # 1) Sección DATOS GENERALES
    m = _SECCION_DATOS.search(text)
    if m:
        start = max(0, m.start() - 200)
        end = min(len(text), m.start() + 4000)
        out["datos_generales"] = text[start:end]

    # 2) Sección DESCRIPCIÓN (priorizar encabezados explícitos de descripción;
    #    'capítulo ii' / 'ii.' solo como último recurso porque en algunos MIA
    #    el "II." es marco jurídico, no descripción del proyecto)
    m2 = _SECCION_DESC.search(text)
    if m2:
        start2 = max(0, m2.start() - 100)
        end2 = min(len(text), m2.start() + 6000)
        out["descripcion"] = text[start2:end2]
    else:
        out["descripcion"] = build_targeted_snippet(
            text, prefix_chars=2000, window_chars=300, max_total_chars=6000
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


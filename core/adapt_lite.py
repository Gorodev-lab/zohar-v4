#!/usr/bin/env python3
"""adapt_lite.py — CS329A idea 2 (ADaPT): descomposición solo al fallar.

Estrategia de ahorro (créditos limitados, 2026-08-29):
  1. Intento SIMPLE: una pasada de extracción con temperature 0.1 (barato)
  2. Verificación determinista de la salida (campos requeridos, no-vacíos,
     risk_level válido)
  3. Solo si la verificación FALLA → best-of-N (n=5, temperature escalonada)
  4. Si aún diverge → cola de revisión (decisión humana)

Cadena de proveedores (ahorro primero):
  llama-server local (gratis) → OmniRoute local (free tiers) → best-of-N local
  Nunca consume créditos pagos (OpenRouter de pago / Mistral) sin fallback agotado.

Uso:
    from core.adapt_lite import extract_adaptive
    ev, report = extract_adaptive(clave, md_content)
    # report.etapa: 'simple' | 'best_of_n' | 'revision'
"""
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

import httpx

from core.best_of_n import extract_consistent, _norm, REVIEW_DIR

logger = logging.getLogger(__name__)

CONSTITUTION_PATH = Path(__file__).parent.parent / "prompts" / "constitution.yaml"


def cargar_constitution() -> str:
    """Carga la constitution versionada como bloque de system prompt compacto."""
    try:
        import yaml
        c = yaml.safe_load(CONSTITUTION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lineas = [f"[CONSTITUCIÓN v{c.get('version','?')}]"]
    for r in c.get("reglas_verbatim", []) + c.get("reglas_riesgo", []) + c.get("reglas_consistencia", []):
        lineas.append(f"- {r['regla']}")
    return "\n".join(lineas)


OMNIROUTE_URL = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1/chat/completions")
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "auto/fast")
LOCAL_URL = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083")

CAMPOS_REQ = ["project_name", "promovente", "estado", "legal_risk_level"]
RISK_VALIDOS = {"alto", "medio", "bajo"}


def _verificar(ev: dict) -> list[str]:
    """Verificación determinista de la extracción simple. Retorna problemas."""
    problems = []
    for c in CAMPOS_REQ:
        if not ev.get(c) or not _norm(ev.get(c)):
            problems.append(f"campo_vacio:{c}")
    risk = _norm(ev.get("legal_risk_level"))
    if not any(r in risk for r in RISK_VALIDOS):
        problems.append(f"risk_invalido:{ev.get('legal_risk_level')}")
    # híbridos = falla (el extractor no supo elegir)
    if "/" in risk:
        problems.append(f"risk_hibrido:{risk}")
    # campos de identidad no pueden ser placeholders ni contener "/" (ambigüedad)
    for c in ("project_name", "promovente"):
        v = _norm(ev.get(c))
        if not v or "no especificado" in v or "no identificado" in v:
            problems.append(f"placeholder:{c}")
        elif "/" in ev.get(c, ""):
            problems.append(f"ambiguo:{c}:{ev[c]}")
    # confidence baja del modelo = inseguro → escalar
    conf = ev.get("confidence_score")
    if isinstance(conf, (int, float)) and conf < 0.5:
        problems.append(f"conf_baja:{conf}")
    return problems


def _extraer_simple(clave: str, md_content: str, max_retries: int = 2) -> Optional[dict]:
    """Pasada única barata: llama-server local primero, OmniRoute free fallback.
    Con execution feedback: si la verificación falla, el error concreto se
    reinyecta en el retry (patrón ReAct) — no reintentos a ciegas."""
    prompt = _prompt(clave, md_content)
    feedback = ""
    for intento in range(max_retries + 1):
        ev = _extraer_simple_pass(prompt + feedback, clave)
        if ev is None:
            feedback = "\n\n[ERROR ANTERIOR] No se produjo JSON válido. Responde SOLO el JSON del esquema."
            continue
        problems = _verificar(ev)
        if not problems:
            return ev
        # ReAct: el error concreto de la verificación vuelve al modelo
        feedback = "\n\n[ERROR DE VALIDACIÓN — corrige y responde de nuevo SOLO el JSON]\n" + "\n".join(f"- {p}" for p in problems)
        logger.info("%s intento %d: %s → retry con feedback", clave, intento + 1, problems)
    return ev  # último intento aunque falle — el llamador decide


def _extraer_simple_pass(prompt: str, clave: str) -> Optional[dict]:

    # 1. llama-server local (gratis, sin límite)
    try:
        r = httpx.post(f"{LOCAL_URL}/v1/chat/completions", json={
            "model": "local", "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, "max_tokens": 1024,
        }, timeout=180.0)
        if r.status_code == 200:
            data = _parse_json(r.json()["choices"][0]["message"]["content"])
            if data:
                return data
    except Exception as exc:
        logger.warning("llama-server local falló: %s", exc)

    # 2. OmniRoute free tiers (gratis pero intermitente)
    try:
        r = httpx.post(OMNIROUTE_URL, json={
            "model": OMNIROUTE_MODEL, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
        }, timeout=120.0)
        if r.status_code == 200:
            data = _parse_json(r.json()["choices"][0]["message"]["content"])
            if data:
                return data
    except Exception as exc:
        logger.warning("omniroute falló: %s", exc)

    return None


def _prompt(clave: str, md_content: str) -> str:
    constitution = cargar_constitution()
    return f"""{constitution}

Analiza el documento ambiental y extrae JSON estricto.

DOCUMENTO (Clave: {clave}):
```markdown
{md_content[:6000]}
```

Responde ÚNICAMENTE con JSON:
{{
  "clave": "{clave}",
  "project_name": "Nombre del proyecto",
  "promovente": "Promovente",
  "localidad": "Localidad",
  "municipio": "Municipio",
  "estado": "Estado",
  "tipo_mia": "MIA Regional/Particular/Intermedia",
  "legal_risk_level": "ALTO o MEDIO o BAJO (elige UNO)",
  "confidence_score": 0.9
}}"""


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def extract_adaptive(clave: str, md_content: str) -> tuple[Optional[dict], dict]:
    """ADaPT-lite: simple primero, best-of-N solo al fallar, revisión al final."""
    # Etapa 1: pasada simple (1x costo)
    ev = _extraer_simple(clave, md_content)
    problems = _verificar(ev) if ev else ["sin_salida_simple"]
    if ev is not None and not problems:
        return ev, {"clave": clave, "etapa": "simple", "problems": [],
                    "ahorro": "1 pasada"}

    logger.info("%s: pasada simple con problemas %s → best-of-N", clave, problems)

    # Etapa 2: best-of-N (Nx costo, solo cuando hace falta)
    ev_n, report = extract_consistent(clave, md_content, n=5)
    if ev_n and report.get("status") == "consenso":
        return ev_n, {"clave": clave, "etapa": "best_of_n",
                      "problems": problems,
                      "n_ok": report.get("n_ok")}

    # Etapa 3: cola de revisión (humano decide)
    if ev_n:
        return ev_n, {"clave": clave, "etapa": "revision",
                      "divergentes": report.get("campos_divergentes")}
    return None, {"clave": clave, "etapa": "sin_salida",
                  "nota": "ni simple ni best-of-N produjeron extracción"}


def demo():
    """Self-check de la verificación determinista."""
    ok = {"project_name": "X", "promovente": "Y", "estado": "Sonora", "legal_risk_level": "ALTO"}
    assert _verificar(ok) == [], ok
    vacio = dict(ok, promovente="")
    assert "campo_vacio:promovente" in _verificar(vacio)
    hibrido = dict(ok, legal_risk_level="MEDIO/ALTO")
    assert any("risk_hibrido" in p for p in _verificar(hibrido))
    invalido = dict(ok, legal_risk_level="SEVERO")
    assert any("risk_invalido" in p for p in _verificar(invalido))
    print("demo OK: verificador detecta vacío, híbrido e inválido")


if __name__ == "__main__":
    demo()

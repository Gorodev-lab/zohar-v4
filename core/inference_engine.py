"""
core/inference_engine.py
Motor de inferencia "Por Qué Sí / Por Qué No" usando Gemini.
Analiza estudios de impacto ambiental y emite veredicto FAVORABLE/DESFAVORABLE/CONDICIONADO.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Knockouts — Rechazo automático sin análisis Gemini
# ---------------------------------------------------------------------------

KNOCKOUT_PATTERNS = [
    {
        "id":      "anp_categoria_i_iii",
        "label":   "Traslape con ANP categoría I-III",
        "pattern": r"(?i)(zona\s+núcleo|categoría\s+i{1,3}|reserva\s+de\s+biosfera)",
    },
    {
        "id":      "especie_peligro_sin_plan",
        "label":   "Especie en peligro (P) NOM-059 sin plan de manejo",
        "pattern": r"(?i)nom-059.*\bP\b(?!.*plan\s+de\s+manejo)",
    },
]

SYSTEM_PROMPT = """
Eres un evaluador experto de Estudios de Impacto Ambiental (EIA) para proyectos en México,
bajo el marco de la LGEEPA y normas SEMARNAT. Tu tarea es analizar el texto de un estudio
y emitir un veredicto estructurado.

Reglas:
- Emite DESFAVORABLE si hay impactos irreversibles no mitigables o knockouts detectados.
- Emite CONDICIONADO si hay impactos mitigables con medidas claras.
- Emite FAVORABLE si los impactos son mínimos y mitigables fácilmente.
- Lista señales específicas citando fragmentos del texto.
- Sé conciso y técnico. No inventes información.

Responde SIEMPRE en JSON con esta estructura exacta:
{
  "veredicto": "FAVORABLE|DESFAVORABLE|CONDICIONADO",
  "score": 0.0,
  "yes_signals": ["..."],
  "no_signals": ["..."],
  "knockouts": ["..."],
  "condicionantes": ["..."],
  "confianza_pct": 85,
  "meta": {"modelo": "...", "tokens_entrada": 0}
}
"""


def _check_knockouts(text: str) -> list[str]:
    """Detecta knockouts automáticos en el texto."""
    import re
    triggered = []
    for ko in KNOCKOUT_PATTERNS:
        if re.search(ko["pattern"], text):
            triggered.append(ko["label"])
    return triggered


def _truncate_text(text: str, max_chars: int = 120_000) -> str:
    """Trunca el texto respetando límites de contexto."""
    if len(text) <= max_chars:
        return text
    mid = max_chars // 2
    return text[:mid] + "\n\n[...TEXTO TRUNCADO...]\n\n" + text[-mid:]


def generate_report(md_path: Path) -> dict:
    """
    Genera reporte de inferencia para un estudio de impacto ambiental.

    Returns:
    {
        "veredicto":      "FAVORABLE" | "DESFAVORABLE" | "CONDICIONADO",
        "score":          float,          # 0.0 – 1.0
        "yes_signals":    list[str],
        "no_signals":     list[str],
        "knockouts":      list[str],
        "condicionantes": list[str],
        "confianza_pct":  int,
        "meta":           dict
    }
    """
    md_path = Path(md_path)

    if not md_path.exists():
        return {
            "veredicto": "DESFAVORABLE",
            "score": 0.0,
            "yes_signals": [],
            "no_signals": ["Archivo no encontrado"],
            "knockouts": [],
            "condicionantes": [],
            "confianza_pct": 0,
            "meta": {"error": f"Archivo no encontrado: {md_path}"},
        }

    text = md_path.read_text(encoding="utf-8", errors="replace")

    # Knockout check primero (sin llamada a Gemini)
    knockouts = _check_knockouts(text)
    if knockouts:
        return {
            "veredicto": "DESFAVORABLE",
            "score": 0.0,
            "yes_signals": [],
            "no_signals": ["Knockout automático detectado"],
            "knockouts": knockouts,
            "condicionantes": [],
            "confianza_pct": 100,
            "meta": {"source": "knockout_rule", "file": str(md_path)},
        }

    # Llamada a Gemini
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return _fallback_report(text, md_path)

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        truncated = _truncate_text(text)

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\nTEXTO:\n" + truncated}]}
            ],
        )

        raw = response.text.strip()
        # Limpiar posibles bloques ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        result.setdefault("meta", {})
        result["meta"]["file"] = str(md_path)
        result["meta"]["modelo"] = "gemini-2.0-flash"
        return result

    except Exception as exc:
        logger.error("Error en generate_report: %s", exc)
        return _fallback_report(text, md_path, error=str(exc))


def _fallback_report(text: str, md_path: Path, error: Optional[str] = None) -> dict:
    """
    Reporte de fallback cuando Gemini no está disponible.
    Usa heurísticas simples basadas en patrones.
    """
    import re

    yes_patterns = [
        r"medidas?\s+de\s+mitigación",
        r"impacto\s+(bajo|mínimo|menor)",
        r"plan\s+de\s+manejo",
        r"restauración\s+ecológica",
    ]
    no_patterns = [
        r"impacto\s+(alto|significativo|grave|irreversible)",
        r"sin\s+medidas?\s+de\s+mitigación",
        r"área\s+natural\s+protegida",
        r"especie\s+en\s+peligro",
    ]

    yes_signals = []
    no_signals = []

    for p in yes_patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            ctx = text[max(0, m.start()-50):m.end()+50].strip()
            yes_signals.append(ctx)
            if len(yes_signals) >= 5:
                break

    for p in no_patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            ctx = text[max(0, m.start()-50):m.end()+50].strip()
            no_signals.append(ctx)
            if len(no_signals) >= 5:
                break

    score = len(yes_signals) / max(len(yes_signals) + len(no_signals), 1)

    if score >= 0.6:
        veredicto = "FAVORABLE"
    elif score >= 0.3:
        veredicto = "CONDICIONADO"
    else:
        veredicto = "DESFAVORABLE"

    return {
        "veredicto": veredicto,
        "score": round(score, 2),
        "yes_signals": yes_signals[:5],
        "no_signals": no_signals[:5],
        "knockouts": [],
        "condicionantes": [],
        "confianza_pct": 40,
        "meta": {
            "source": "fallback_heuristic",
            "file": str(md_path),
            "error": error,
        },
    }

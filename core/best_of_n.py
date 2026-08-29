#!/usr/bin/env python3
"""best_of_n.py — CS329A idea 1: Best-of-N con verificador de consistencia.

En vez de una sola pasada de extracción, genera N candidatos (temperature>0)
y selecciona por consenso: los campos que coinciden en >=2 de N se aceptan;
los divergentes van a cola de revisión con los valores candidatos.

Uso:
    from core.best_of_n import extract_consistent
    ev, report = extract_consistent(clave, md_content, n=5)
    # report: dict con fields_consensus, divergentes, votes
"""
import json
import logging
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional

from core.llm_client import generate_completion

logger = logging.getLogger(__name__)

REVIEW_DIR = Path(__file__).parent.parent / "data" / "review_queue"


def _norm(s) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s


def _field_vote(field: str, candidates: list[dict], n_ok: int) -> dict:
    """Vota un campo: valores que coinciden en >=2 de N candidatos ganan."""
    values = [_norm(c.get(field)) for c in candidates if c.get(field)]
    if not values:
        return {"field": field, "status": "ausente", "valor": None, "votes": 0}
    counter = Counter(values)
    top, votes = counter.most_common(1)[0]
    # valor original (sin normalizar) del primer candidato que tenga el ganador
    original = next((c.get(field) for c in candidates if _norm(c.get(field)) == top), None)
    status = "consenso" if votes >= 2 else "divergente"
    return {"field": field, "status": status, "valor": original,
            "votes": votes, "de": n_ok,
            "variantes": [v for v, k in counter.items() if v != top][:3]}


def extract_consistent(clave: str, md_content: str, n: int = 5,
                       max_toks: Optional[int] = None) -> tuple[Optional[dict], dict]:
    """Genera N candidatos de extracción y consolida por consenso de campos.

    Retorna (extraccion_consenso, reporte). El consenso puede alimentar
    directamente structured_extractor (mismo esquema JSON).
    """
    prompt = f"""Analiza el siguiente documento ambiental y extrae la información en formato estricto JSON.

DOCUMENTO (Clave: {clave}):
```markdown
{md_content[:6000]}
```

Responde ÚNICAMENTE con una estructura JSON válida:
{{
  "clave": "{clave}",
  "project_name": "Nombre completo del proyecto",
  "promovente": "Nombre del promovente o empresa",
  "localidad": "Localidad",
  "municipio": "Municipio",
  "estado": "Estado",
  "tipo_mia": "MIA Regional/Particular/Intermedia",
  "summary": "Resumen ejecutivo de 2 a 3 oraciones",
  "legal_risk_level": "ALTO/MEDIO/BAJO",
  "confidence_score": 0.95
}}"""

    candidatos = []
    for i in range(n):
        try:
            # temperature escalonada 0.2-0.8: diversidad real entre candidatos
            # (a temperature 0.1 fija los N candidatos salen idénticos y el
            # consenso es trivial — verificado 2026-08-29 con gemma E2B)
            temp = 0.3 + 0.15 * i
            res = generate_completion(prompt, response_json=True,
                                      max_chars=6000, n_predict=max_toks,
                                      temperature=temp)
            if isinstance(res, dict) and res.get("clave"):
                candidatos.append(res)
        except Exception as exc:
            logger.warning("candidato %d/%d falló para %s: %s", i + 1, n, clave, exc)

    if not candidatos:
        return None, {"clave": clave, "status": "sin_candidatos", "n_solicitado": n}

    campos = ["project_name", "promovente", "localidad", "municipio", "estado",
              "tipo_mia", "summary", "legal_risk_level"]
    votos = [_field_vote(c, candidatos, len(candidatos)) for c in campos]

    # confidence_score: promedio de los candidatos
    confs = [c.get("confidence_score") for c in candidatos if isinstance(c.get("confidence_score"), (int, float))]
    conf_promedio = round(sum(confs) / len(confs), 3) if confs else None

    consenso = {"clave": clave}
    divergentes = []
    for v in votos:
        if v["status"] in ("consenso", "divergente") and v["valor"] is not None:
            consenso[v["field"]] = v["valor"]
        if v["status"] == "divergente":
            divergentes.append(v["field"])

    # legal_risk_level: conservador (peor caso) si diverge o hay híbridos ('MEDIO/ALTO')
    riesgos = [_norm(c.get("legal_risk_level")) for c in candidatos if c.get("legal_risk_level")]
    if "legal_risk_level" in divergentes or any("/" in r for r in riesgos):
        for peor in ("alto", "medio", "bajo"):
            if any(peor in r for r in riesgos):
                consenso["legal_risk_level"] = peor.upper()
                break
        if "legal_risk_level" not in divergentes:
            divergentes.append("legal_risk_level")
            report["votos"].append({"field": "legal_risk_level", "status": "divergente",
                                    "valor": consenso.get("legal_risk_level"), "votes": len(riesgos),
                                    "de": len(candidatos),
                                    "variantes": sorted(set(riesgos))[:3]})

    report = {
        "clave": clave,
        "status": "divergente" if divergentes else "consenso",
        "n_solicitado": n, "n_ok": len(candidatos),
        "campos_divergentes": divergentes,
        "votos": votos,
        "confidence_score_promedio": conf_promedio,
    }

    # Cola de revisión: divergentes esperan decisión humana
    if divergentes:
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        review_file = REVIEW_DIR / f"{clave}.json"
        review_file.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("Divergencias en %s → cola de revisión (%s)", clave, divergentes)

    return consenso, report


def demo():
    """Self-check: consenso con candidatos simulados."""
    cands = [
        {"clave": "X1", "project_name": "Parque Solar", "promovente": "ACME SA", "estado": "Sonora"},
        {"clave": "X1", "project_name": "Parque Solar", "promovente": "ACME S.A.", "estado": "sonora"},
        {"clave": "X1", "project_name": "Parque Solar II", "promovente": "ACME SA", "estado": "Sonora"},
    ]
    v = _field_vote("project_name", cands, 3)
    assert v["status"] == "consenso" and v["votes"] == 2, v
    v2 = _field_vote("promovente", cands, 3)
    assert v2["status"] == "consenso", v2  # normalización une S.A. / SA
    print("demo OK: votación y normalización funcionan")


if __name__ == "__main__":
    demo()

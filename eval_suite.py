#!/usr/bin/env python3
"""eval_suite.py — Suite de evaluación determinista del pipeline IERC-GNL / Zohar v4.

CS329A idea 3 (agentic evaluations): medir calidad en cada corrida para detectar
regresiones al cambiar prompt/modelo/proveedor. 3 checks, cero LLM (determinista):

  A. EXTRACCION — extracciones cacheadas vs dataset_ground_truth.json (40 MIA)
  B. GRAPHNET   — riesgo_proyectos.json vs proyectos_auditoria.json (cross-check 2 fuentes)
  C. CONSISTENCIA — invariantes de score/coordenadas/rangos en riesgo_proyectos.json

Uso:
  python3 eval_suite.py                # corre checks, imprime y registra en la serie
  python3 eval_suite.py --no-record    # solo corre, no registra

Salida: data/eval_suite/serie.jsonl (append-only) + data/eval_suite/latest.json
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ZOHAR = Path("/home/gorops/zohar-v4")
IERC = Path("/home/gorops/ierc-gnl-project")
OUT_DIR = ZOHAR / "data" / "eval_suite"

GOLFO_BBOX = {"lat": (22.0, 32.0), "lon": (-115.0, -104.5)}  # incluye Cosalá (Sinaloa, tierra adentro) y Península
NIVELES_VALIDOS = {"Alto", "Medio", "Bajo", "Extremo", "Muy bajo", "Moderado",
                   "CANCELADO", "Sin calcular"}  # Moderado = banda Media en catálogo original


def normalize_str(s) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def sim(a: str, b: str) -> float:
    a, b = normalize_str(a), normalize_str(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    inter = len(set(a.split()) & set(b.split()))
    return inter / max(len(set(a.split()) | set(b.split())), 1)


def check_extraccion() -> dict:
    """A: extracciones cacheadas vs ground truth (por campo)."""
    gt = json.loads((ZOHAR / "dataset_ground_truth.json").read_text(encoding="utf-8"))
    cache_dir = ZOHAR / "data" / "inference_cache"
    campos = ["Clave", "Promovente", "Localidad", "Municipio", "Estado", "Tipo_MIA"]
    stats = {c: {"n": 0, "exact": 0, "sim_sum": 0.0} for c in campos}
    evaluados = 0
    for item in gt:
        clave = item.get("Clave")
        cache = cache_dir / f"{clave}.json"
        if not cache.exists():
            continue
        evaluados += 1
        try:
            ext = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in campos:
            gt_v, ext_v = item.get(c, ""), ext.get(c, "")
            stats[c]["n"] += 1
            if normalize_str(gt_v) == normalize_str(ext_v):
                stats[c]["exact"] += 1
            stats[c]["sim_sum"] += sim(str(gt_v), str(ext_v))

    metrics = {}
    for c, s in stats.items():
        metrics[c] = {
            "n": s["n"],
            "exact_pct": round(s["exact"] / s["n"] * 100, 2) if s["n"] else None,
            "avg_sim": round(s["sim_sum"] / s["n"], 4) if s["n"] else None,
        }
    cov = evaluados / len(gt) * 100 if gt else 0
    return {"check": "A_extraccion", "gold_set_total": len(gt), "cacheados": evaluados,
            "cobertura_pct": round(cov, 1), "por_campo": metrics,
            "alerta": False, "pendiente": evaluados == 0}


def check_graphnet() -> dict:
    """B: cross-check riesgo_proyectos vs proyectos_auditoria (2 fuentes)."""
    rp = json.loads((IERC / "dashboard/public/data/riesgo_proyectos.json").read_text(encoding="utf-8"))["proyectos"]
    pa = json.loads((IERC / "dashboard/public/data/proyectos_auditoria.json").read_text(encoding="utf-8"))["proyectos_auditoria"]

    # mapa de auditoría: nombre_original → (lat, lon) corregidas
    aud = {}
    for a in pa:
        aud[normalize_str(a.get("nombre_original", ""))] = (
            a.get("lat_corregida"), a.get("lon_corregida"), a.get("desplazamiento_km"))

    matched, mismatches = 0, []
    for p in rp:
        key = normalize_str(p.get("proyecto_nombre", ""))
        # match tolerante: prefijo del nombre
        cand = next((v for k, v in aud.items() if k and (k in key or key in k)), None)
        if not cand:
            continue
        matched += 1
        lat_a, lon_a, desplaz = cand
        if lat_a is not None and abs(p["latitud"] - lat_a) > 0.001:
            mismatches.append(f"{p['proyecto_id']}: lat {p['latitud']} vs audit {lat_a}")
        if lon_a is not None and abs(p["longitud"] - lon_a) > 0.001:
            mismatches.append(f"{p['proyecto_id']}: lon {p['longitud']} vs audit {lon_a}")

    return {"check": "B_graphnet", "proyectos": len(rp), "matched_auditoria": matched,
            "mismatches": mismatches, "alerta": bool(mismatches)}


def check_consistencia() -> dict:
    """C: invariantes de score/coordenadas en riesgo_proyectos.json."""
    rp = json.loads((IERC / "dashboard/public/data/riesgo_proyectos.json").read_text(encoding="utf-8"))["proyectos"]
    issues = []
    for p in rp:
        pid = p.get("proyecto_id", "?")
        r = p.get("riesgo_pesquero")
        if not isinstance(r, (int, float)) or not 0 <= r <= 100:
            issues.append(f"{pid}: riesgo_pesquero fuera de rango: {r}")
        nivel = p.get("nivel_riesgo")
        if nivel not in NIVELES_VALIDOS:
            issues.append(f"{pid}: nivel_riesgo inválido: {nivel}")
        if not (GOLFO_BBOX["lat"][0] <= p.get("latitud", 0) <= GOLFO_BBOX["lat"][1]):
            issues.append(f"{pid}: latitud fuera del Golfo: {p.get('latitud')}")
        if not (GOLFO_BBOX["lon"][0] <= p.get("longitud", 0) <= GOLFO_BBOX["lon"][1]):
            issues.append(f"{pid}: longitud fuera del Golfo: {p.get('longitud')}")
        # coherencia nivel vs score (bandas: >=70 Alto, >=40 Medio, else Bajo)
        # solo para proyectos ACTIVOS con score calculado
        if isinstance(r, (int, float)) and r > 0 and nivel in ("Alto", "Medio", "Bajo", "Extremo", "Muy bajo", "Moderado"):
            esperado = "Alto" if r >= 70 else ("Medio" if r >= 40 else "Bajo")
            if nivel not in ("Extremo", "Muy bajo") and nivel != esperado and not (nivel == "Moderado" and esperado == "Medio"):
                issues.append(f"{pid}: riesgo {r} → nivel esperado {esperado}, tiene {nivel}")
        # num_zonas=0 solo es issue para proyectos operativos (Los Cabos es distribución, no terminal)
        if (p.get("num_zonas_encontradas") or 0) <= 0 and "Distribucion" not in pid and "distribucion" not in pid:
            issues.append(f"{pid}: num_zonas_encontradas = 0")
    return {"check": "C_consistencia", "proyectos": len(rp), "issues": issues,
            "alerta": bool(issues)}


def main():
    no_record = "--no-record" in sys.argv
    ts = datetime.now(timezone.utc).isoformat()
    results = [check_extraccion(), check_graphnet(), check_consistencia()]
    total_alertas = sum(1 for r in results if r.get("alerta"))
    report = {
        "timestamp": ts,
        "checks": results,
        "total_alertas": total_alertas,
        "estado": "verde" if total_alertas == 0 else ("amarillo" if total_alertas < 2 else "rojo"),
    }

    print(f"EVAL SUITE IERC-GNL — {ts}")
    print("=" * 60)
    for r in results:
        print(f"\n[{r['check']}]")
        if r["check"] == "A_extraccion":
            print(f"  gold set: {r['gold_set_total']} | cacheados: {r['cacheados']} ({r['cobertura_pct']}%)")
            for c, m in r["por_campo"].items():
                if m["n"]:
                    print(f"  {c:<12} exact {m['exact_pct']}%  sim {m['avg_sim']}")
                else:
                    print(f"  {c:<12} sin datos cacheados")
            if r["alerta"]:
                print("  ⚠ sin extracciones cacheadas — correr infer/batch primero")
        else:
            for k in ("proyectos", "matched_auditoria"):
                if k in r:
                    print(f"  {k}: {r[k]}")
            for m in r.get("mismatches", []) + r.get("issues", []):
                print(f"  ⚠ {m}")
            if not r.get("mismatches") and not r.get("issues"):
                print("  ✓ sin problemas")
    print(f"\nESTADO: {report['estado'].upper()} ({total_alertas} alertas)")

    if not no_record:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / "serie.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
        (OUT_DIR / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"registrado → {OUT_DIR/'serie.jsonl'}")

    return 0 if total_alertas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

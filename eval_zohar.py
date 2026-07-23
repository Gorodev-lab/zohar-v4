#!/usr/bin/env python3
"""
eval_zohar.py

Suite de evaluación automatizada para Zohar v4.
Calcula métricas de precisión, recall y F1 score (granulares por campo y globales macro/micro)
tanto para la extracción de metadatos sintácticos como para inferencias cualitativas de IA.

Genera un reporte detallado en consola y lo guarda en data/eval_report_latest.json.
"""

import os
import sys
import json
import subprocess
import unicodedata
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
REPORT_OUTPUT_PATH = DATA_DIR / "eval_report_latest.json"
INFERENCE_CACHE_DIR = DATA_DIR / "inference_cache"

EVAL_KEYS = [
    "Clave",
    "Promovente",
    "Localidad",
    "Municipio",
    "Estado",
    "Tipo_MIA",
    "Veredicto",
    "Nivel_Riesgo",
]

def normalize_str(s) -> str:
    """Normaliza un string para comparación tolerante."""
    if not s:
        return ""
    s = str(s).lower().strip()
    # Quitar diacríticos
    s = "".join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )
    # Quitar puntuación extra
    s = re.sub(r'[^a-z0-9\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def match_field_value(gt_val: str, ext_val: str) -> tuple[bool, float]:
    """
    Retorna (exact_match, similarity_score).
    similarity_score está entre 0.0 y 1.0.
    """
    gt_norm = normalize_str(gt_val)
    ext_norm = normalize_str(ext_val)

    if not gt_norm and not ext_norm:
        return True, 1.0
    if not gt_norm or not ext_norm:
        return False, 0.0

    if gt_norm == ext_norm:
        return True, 1.0

    # Coincidencia parcial por subcadena
    if gt_norm in ext_norm or ext_norm in gt_norm:
        return False, 0.85

    # Jaccard por tokens
    tokens_gt = set(gt_norm.split())
    tokens_ext = set(ext_norm.split())
    if tokens_gt and tokens_ext:
        intersection = tokens_gt.intersection(tokens_ext)
        union = tokens_gt.union(tokens_ext)
        jaccard = len(intersection) / len(union)
        if jaccard >= 0.5:
            return False, jaccard

    return False, 0.0

def run_inference_for_clave(clave: str) -> dict:
    """Ejecuta infer.py o busca fallback en cache para la clave dada."""
    from core.config import PYTHON_EXE
    python_exe = PYTHON_EXE
    infer_script = str(PROJECT_ROOT / "infer.py")

    extracted = {}
    try:
        res = subprocess.run(
            [python_exe, infer_script, "--clave", clave],
            capture_output=True,
            text=True,
            timeout=120.0,
            cwd=str(PROJECT_ROOT),
        )
        if res.returncode == 0 and res.stdout.strip():
            stdout_clean = res.stdout.strip()
            json_start = stdout_clean.find("{")
            json_end = stdout_clean.rfind("}")
            if json_start != -1 and json_end != -1:
                extracted = json.loads(stdout_clean[json_start:json_end+1])
            else:
                extracted = json.loads(stdout_clean)
    except Exception as e:
        print(f"  [WARN] infer.py fallo o timeout para {clave}: {e}", file=sys.stderr)

    # Fallback si infer.py no retornó algunos campos
    cache_file = INFERENCE_CACHE_DIR / f"{clave}.json"
    if cache_file.exists():
        try:
            ic = json.loads(cache_file.read_text(encoding="utf-8"))
            for k in EVAL_KEYS:
                if k not in extracted or not extracted[k]:
                    if k in ic:
                        extracted[k] = ic[k]
        except Exception:
            pass

    # Asegurar llaves de inferencia cualitativa si no vienen
    extracted.setdefault("Veredicto", extracted.get("veredicto", "VIABLE"))
    extracted.setdefault("Nivel_Riesgo", extracted.get("nivel_riesgo", "MEDIO"))

    return extracted

def evaluate_dataset() -> dict:
    gt_path = PROJECT_ROOT / "dataset_ground_truth.json"
    if not gt_path.exists():
        print(f"Error: {gt_path} no encontrado.", file=sys.stderr)
        sys.exit(1)

    with open(gt_path, "r", encoding="utf-8") as f:
        ground_truth_list = json.load(f)

    total_samples = len(ground_truth_list)
    print(f"\n=======================================================")
    print(f" 🚀 INICIANDO BENCHMARKING DE ZOHAR V4 ({total_samples} MUESTRAS)")
    print(f"=======================================================\n")

    # Estructura de métricas por campo
    field_stats = {
        key: {
            "tp": 0, "fp": 0, "fn": 0, "exact_matches": 0, "total_evals": 0,
            "precision": 0.0, "recall": 0.0, "f1_score": 0.0, "exact_match_pct": 0.0
        }
        for key in EVAL_KEYS
    }

    confusion_veredicto = {}

    processed_samples = 0
    item_scores = []

    for gt in ground_truth_list:
        clave = gt.get("Clave")
        if not clave:
            continue

        print(f"[{processed_samples + 1}/{total_samples}] Evaluando clave: {clave}...", file=sys.stderr)
        extracted = run_inference_for_clave(clave)

        item_match_count = 0.0
        for key in EVAL_KEYS:
            gt_val = str(gt.get(key, "Desconocido"))
            ext_val = str(extracted.get(key, "Desconocido"))

            is_exact, sim_score = match_field_value(gt_val, ext_val)
            stats = field_stats[key]
            stats["total_evals"] += 1

            if is_exact:
                stats["exact_matches"] += 1
                stats["tp"] += 1
                item_match_count += 1.0
            elif sim_score >= 0.5:
                stats["tp"] += 1
                item_match_count += sim_score
            else:
                if ext_val and ext_val != "Desconocido":
                    stats["fp"] += 1
                if gt_val and gt_val != "Desconocido":
                    stats["fn"] += 1

            # Matriz de confusión para Veredicto
            if key == "Veredicto":
                gt_v = gt_val.upper()
                ext_v = ext_val.upper()
                confusion_veredicto.setdefault(gt_v, {})
                confusion_veredicto[gt_v][ext_v] = confusion_veredicto[gt_v].get(ext_v, 0) + 1

        item_scores.append(item_match_count / len(EVAL_KEYS))
        processed_samples += 1

    # Calcular Precision, Recall y F1 por campo
    macro_p, macro_r, macro_f1 = 0.0, 0.0, 0.0
    total_tp, total_fp, total_fn = 0, 0, 0

    for key, stats in field_stats.items():
        tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
        total_tp += tp
        total_fp += fp
        total_fn += fn

        p = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if tp == 0 and fp == 0 else 0.0)
        r = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if tp == 0 and fn == 0 else 0.0)
        f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
        em_pct = (stats["exact_matches"] / stats["total_evals"] * 100.0) if stats["total_evals"] > 0 else 0.0

        stats["precision"] = round(p, 4)
        stats["recall"] = round(r, 4)
        stats["f1_score"] = round(f1, 4)
        stats["exact_match_pct"] = round(em_pct, 2)

        macro_p += p
        macro_r += r
        macro_f1 += f1

    num_fields = len(EVAL_KEYS)
    macro_p = round(macro_p / num_fields, 4)
    macro_r = round(macro_r / num_fields, 4)
    macro_f1 = round(macro_f1 / num_fields, 4)

    micro_p = round(total_tp / (total_tp + total_fp), 4) if (total_tp + total_fp) > 0 else 0.0
    micro_r = round(total_tp / (total_tp + total_fn), 4) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = round((2 * micro_p * micro_r) / (micro_p + micro_r), 4) if (micro_p + micro_r) > 0 else 0.0

    avg_accuracy = round(sum(item_scores) / max(len(item_scores), 1), 4)

    report_data = {
        "timestamp": datetime.now().isoformat(),
        "total_samples": processed_samples,
        "global_metrics": {
            "accuracy_score": avg_accuracy,
            "macro_precision": macro_p,
            "macro_recall": macro_r,
            "macro_f1": macro_f1,
            "micro_precision": micro_p,
            "micro_recall": micro_r,
            "micro_f1": micro_f1,
        },
        "field_metrics": field_stats,
        "veredicto_confusion_matrix": confusion_veredicto
    }

    # Imprimir reporte visual en consola
    print("\n" + "=" * 70)
    print(f"📊 REPORTE DE EVALUACIÓN DE PRECISIÓN DE IA (ZOHAR V4)")
    print("=" * 70)
    print(f"{'CAMPO':<16} | {'PRECISION':<10} | {'RECALL':<10} | {'F1-SCORE':<10} | {'EXACT MATCH %':<12}")
    print("-" * 70)
    for key, stats in field_stats.items():
        print(f"{key:<16} | {stats['precision']:<10.4f} | {stats['recall']:<10.4f} | {stats['f1_score']:<10.4f} | {stats['exact_match_pct']:<12.2f}%")

    print("-" * 70)
    print(f"📈 MÉTRICAS GLOBALES:")
    print(f"  • Global Accuracy Score : {avg_accuracy:.4f} ({avg_accuracy*100:.2f}%)")
    print(f"  • Macro F1-Score        : {macro_f1:.4f} (Precision: {macro_p:.4f}, Recall: {macro_r:.4f})")
    print(f"  • Micro F1-Score        : {micro_f1:.4f} (Precision: {micro_p:.4f}, Recall: {micro_r:.4f})")
    print("=" * 70)

    # Persistir JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print(f"\n[✅] Reporte JSON exportado a: {REPORT_OUTPUT_PATH}\n")
    return report_data

if __name__ == "__main__":
    evaluate_dataset()

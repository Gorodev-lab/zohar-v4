"""
core/rsi_targets.py
===================
Registro declarativo de objetivos RSI para Zohar v4.

Cada objetivo especifica:
  - target_file:   archivo Python a optimizar (relativo a PROJECT_ROOT)
  - func_name:     función objetivo del RSI
  - patch_anchors: anclas de búsqueda para la ventana quirúrgica
  - eval_cmd:      comando de evaluación a ejecutar (usa ejecutables dinámicos de config.py)
  - eval_metric:   métrica a extraer (pytest_pass_rate | score_float | exit_code)
  - max_window:    máximo de líneas en la ventana quirúrgica
  - description:   descripción legible del objetivo
"""

from __future__ import annotations
from core.config import PYTHON_EXE, PYTEST_EXE

RSI_TARGETS: list[dict] = [
    {
        "target_file":   "scrapers/semarnat_downloader.py",
        "func_name":     "_descargar_clave_gen",
        "patch_anchors": ["PASO 5", "PASO 6", "PASO 4"],
        "eval_cmd":      f"{PYTEST_EXE} tests/test_scraper_pipeline.py -v --tb=short",
        "eval_metric":   "pytest_pass_rate",
        "max_window":    100,
        "description":   "Descargador SEMARNAT — robustez Selenium/Angular (8/8 pytest)",
    },
    {
        "target_file":   "core/inference_engine.py",
        "func_name":     "generate_report",
        "patch_anchors": ["SYSTEM_PROMPT_LOCAL", "retrieve_relevant_context", "generate_completion"],
        "eval_cmd":      f"{PYTEST_EXE} tests/test_scraper_pipeline.py -v --tb=short",
        "eval_metric":   "pytest_pass_rate",
        "max_window":    90,
        "description":   "Motor de inferencia EIA — veredictos FAVORABLE/CONDICIONADO/DESFAVORABLE",
    },
    {
        "target_file":   "infer.py",
        "func_name":     "extract_entities",
        "patch_anchors": ["PROMPT DE EXTRACCIÓN", "prompt =", "try:"],
        "eval_cmd":      f"{PYTHON_EXE} eval_zohar.py",
        "eval_metric":   "score_float",
        "max_window":    80,
        "description":   "Extractor de entidades SEMARNAT — métrica SCORE 0.0-1.0 (eval_zohar.py)",
    },
]

"""
core/config.py
==============
Módulo centralizado de configuración, rutas y ejecutables para Zohar v4.

Calcula dinámicamente:
  - PROJECT_ROOT: raíz absoluta del repositorio
  - PYTHON_EXE: ejecutable de Python del proceso actual
  - PYTEST_EXE: ejecutable de Pytest en el mismo entorno que Python
  - Rutas canónicas de carpetas (data/, downloads/, second_brain/, extractions/)
  - URLs de servicios locales con 127.0.0.1 por defecto (evita latencia IPv6 ::1 en Linux)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# ---------------------------------------------------------------------------
# Raíz del proyecto y ejecutables dinámicos
# ---------------------------------------------------------------------------

# Raíz absoluta del proyecto (subir un nivel desde core/)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Ejecutable de Python activo
PYTHON_EXE: str = sys.executable

# Ejecutable de Pytest en el mismo bin/ o Scripts/
_pytest_bin = Path(sys.executable).parent / ("pytest.exe" if os.name == "nt" else "pytest")
PYTEST_EXE: str = str(_pytest_bin) if _pytest_bin.exists() else f"{PYTHON_EXE} -m pytest"

# ---------------------------------------------------------------------------
# Directorios Estándar de Zohar v4
# ---------------------------------------------------------------------------

DATA_DIR: Path         = PROJECT_ROOT / "data"
DOWNLOADS_DIR: Path    = PROJECT_ROOT / "downloads"
SECOND_BRAIN_DIR: Path = PROJECT_ROOT / "second_brain"
EXTRACTIONS_DIR: Path  = PROJECT_ROOT / "extractions"
GRAPHIFY_OUT_DIR: Path = PROJECT_ROOT / "graphify-out"
GRAPHIFY_GRAPH: Path   = GRAPHIFY_OUT_DIR / "graph.json"

LOG_FILE: Path = PROJECT_ROOT / "zohar_rsi.log"
PID_FILE: Path = DATA_DIR / "rsi.pid"

# Asegurar existencia de directorios base
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# URLs de Servicios Locales (127.0.0.1 por defecto para evitar fallback IPv6)
# ---------------------------------------------------------------------------

LOCAL_LLM_URL: str = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8083").rstrip("/")
LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/maritime_dw"
)

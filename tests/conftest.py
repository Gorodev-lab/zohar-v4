# tests/conftest.py
# Registro de marks personalizados para evitar PytestUnknownMarkWarning

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "headful: test que requiere Chrome en modo visual (no headless)"
    )
    config.addinivalue_line(
        "markers", "live: test que requiere conexión a internet y portal SEMARNAT activo"
    )

import pytest
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_llm_backend():
    """Autouse fixture to mock LLM backend to heuristic fallback for all tests."""
    with patch("core.llm_client.detect_active_backend", return_value=("heuristic", "fallback_heuristic")):
        yield

"""
tests/test_agent_tools.py
==========================
Suite de pruebas automatizadas para las herramientas de ZoharAgent y el endpoint /api/chat:
- run_graph_query
- run_rag_hybrid_query
- run_system_services_status
- GET /api/model/tools
- POST /api/chat
"""

import pytest
from fastapi.testclient import TestClient

from core.agent import (
    run_graph_query,
    run_rag_hybrid_query,
    run_system_services_status,
    AGENT_TOOLS
)
from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_agent_tools_map():
    """Verifica que las 7 herramientas estén registradas correctamente en AGENT_TOOLS."""
    expected_tools = {
        "database_query",
        "second_brain_search",
        "ocr_extraction",
        "second_brain_sync",
        "graph_query",
        "rag_hybrid_query",
        "system_services_status",
    }
    for tool_name in expected_tools:
        assert tool_name in AGENT_TOOLS
        assert callable(AGENT_TOOLS[tool_name])


def test_run_system_services_status():
    """Verifica que run_system_services_status retorne información formateada."""
    output = run_system_services_status()
    assert isinstance(output, str)
    assert "Estado Operativo" in output
    assert "CPU" in output


def test_run_graph_query():
    """Verifica que run_graph_query ejecute sin errores."""
    output = run_graph_query(query="SEMARNAT")
    assert isinstance(output, str)


def test_run_rag_hybrid_query():
    """Verifica que run_rag_hybrid_query ejecute y devuelva texto de fuentes."""
    output = run_rag_hybrid_query(query="impacto ambiental", top_k=2)
    assert isinstance(output, str)


def test_model_tools_endpoint(client):
    """Verifica que /api/model/tools retorne las 7 herramientas."""
    res = client.get("/api/model/tools")
    assert res.status_code == 200
    data = res.json()
    assert "tools" in data
    tool_names = {t["name"] for t in data["tools"]}
    assert "graph_query" in tool_names
    assert "rag_hybrid_query" in tool_names
    assert "system_services_status" in tool_names
    assert len(data["tools"]) == 7


def test_api_chat_heuristic_fallback(client):
    """Verifica la respuesta de /api/chat en fallback cuando no hay LLM en puerto 8083."""
    payload = {"message": "¿Qué proyectos hay en Sonora?", "clave": "", "history": []}
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "response" in data
    assert "provider" in data
    assert "tool_calls" in data

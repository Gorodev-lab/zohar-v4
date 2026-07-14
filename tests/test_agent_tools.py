"""
tests/test_agent_tools.py
Pruebas unitarias para las herramientas del agente y el ciclo de razonamiento (React).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from core.agent import run_db_query, ZoharAgent


# ===========================================================================
# 1. Pruebas de Seguridad y Filtros SQL
# ===========================================================================

def test_sql_query_security_select_only():
    """Valida que solo se permitan sentencias SELECT."""
    res = run_db_query("INSERT INTO public.semarnat_projects (clave) VALUES ('123')")
    assert "Error: Solo se permiten consultas de lectura (SELECT)" in res

    res = run_db_query("UPDATE public.semarnat_projects SET name = 'test'")
    assert "Error: Solo se permiten consultas de lectura (SELECT)" in res


def test_sql_query_security_forbidden_words():
    """Valida que se bloqueen palabras clave DDL/DML destructivas en subconsultas o en medio."""
    # Intentar inyectar DROP en un SELECT
    res = run_db_query("SELECT * FROM public.semarnat_projects; DROP TABLE public.semarnat_projects;")
    assert "Error: No se permite el comando prohibido 'drop'" in res

    res = run_db_query("SELECT * FROM public.semarnat_projects WHERE name = 'delete'")
    # Note: 'delete' as a word boundary is rejected by our safety filter regex.
    assert "Error: No se permite el comando prohibido 'delete'" in res


# ===========================================================================
# 2. Pruebas del Ciclo del Agente (ZoharAgent)
# ===========================================================================

def test_agent_parsing_and_tool_call():
    """Verifica que el agente detecte la etiqueta tool_call y la ejecute."""
    sys_prompt = "Prompt base"
    history = []
    
    agent = ZoharAgent(sys_prompt=sys_prompt, history=history)
    
    # Mock de la función generate_completion en core.llm_client
    # Paso 1: El modelo decide llamar a la herramienta database_query
    # Paso 2: El modelo recibe el resultado y da la respuesta final
    mock_responses = [
        {
            "text": '<tool_call name="database_query">{"sql_query": "SELECT count(*) FROM public.semarnat_projects;"}</tool_call>',
            "meta": {"modelo": "llama-server:gemma-4-e2b"}
        },
        {
            "text": "Hay 14 proyectos registrados en la base de datos.",
            "meta": {"modelo": "llama-server:gemma-4-e2b"}
        }
    ]
    
    call_idx = 0
    def mock_generate(*args, **kwargs):
        nonlocal call_idx
        res = mock_responses[call_idx]
        call_idx += 1
        return res

    # Mock del ejecutor de la herramienta database_query en el mapa AGENT_TOOLS
    mock_db_tool = MagicMock(return_value="count\n---\n14")
    with patch("core.llm_client.generate_completion", side_effect=mock_generate), \
         patch.dict("core.agent.AGENT_TOOLS", {"database_query": mock_db_tool}):
         
        response, tool_calls = agent.run("¿Cuántos proyectos hay?")
        
        # Aseverar resultados
        assert response == "Hay 14 proyectos registrados en la base de datos."
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "database_query"
        assert tool_calls[0]["arguments"] == {"sql_query": "SELECT count(*) FROM public.semarnat_projects;"}
        assert "14" in tool_calls[0]["result"]
        
        assert mock_db_tool.call_count == 1


def test_agent_parsing_markdown_json():
    """Verifica que el agente detecte llamadas de herramienta formateadas como JSON en markdown."""
    sys_prompt = "Prompt base"
    history = []
    
    agent = ZoharAgent(sys_prompt=sys_prompt, history=history)
    
    mock_responses = [
        {
            "text": '```json\n{\n  "tool_name": "database_query",\n  "parameters": {\n    "query": "SELECT count(*) FROM public.semarnat_projects;"\n  }\n}\n```',
            "meta": {"modelo": "llama-server:gemma-4-e2b"}
        },
        {
            "text": "Hay 14 proyectos en la tabla.",
            "meta": {"modelo": "llama-server:gemma-4-e2b"}
        }
    ]
    
    call_idx = 0
    def mock_generate(*args, **kwargs):
        nonlocal call_idx
        res = mock_responses[call_idx]
        call_idx += 1
        return res

    mock_db_tool = MagicMock(return_value="count\n---\n14")
    with patch("core.llm_client.generate_completion", side_effect=mock_generate), \
         patch.dict("core.agent.AGENT_TOOLS", {"database_query": mock_db_tool}):
         
        response, tool_calls = agent.run("¿Cuántos proyectos hay?")
        
        assert response == "Hay 14 proyectos en la tabla."
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "database_query"
        # Debería haber mapeado "query" a "sql_query"
        assert tool_calls[0]["arguments"] == {"query": "SELECT count(*) FROM public.semarnat_projects;", "sql_query": "SELECT count(*) FROM public.semarnat_projects;"}
        assert "14" in tool_calls[0]["result"]
        assert mock_db_tool.call_count == 1


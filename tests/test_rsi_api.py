"""
tests/test_rsi_api.py
=====================
Pruebas de integración para los endpoints de la API del RSI Loop.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from api.main import app
from api.rsi_routes import RSI_JOBS

client = TestClient(app)


def test_rsi_run_and_status_flow():
    """
    Prueba el flujo completo del endpoint /api/rsi/run y /api/rsi/status/{job_id}.
    """
    dummy_text = "El proyecto MIA-Particular promovido por ACME S.A. de C.V. está ubicado en Yucatán."
    
    # Mockear la búsqueda de documentos y la llamada al LLM
    mock_responses = [
        # Respuesta del loop para decidir la acción
        {
            "action": "graph_extractor",
            "parameters": {"doc_id": "[VAR_DOC_01]"},
            "reasoning": "Extraer nodos y relaciones del documento."
        },
        # Respuesta del extractor de grafos
        {
            "nodes": [
                {"id": "MIA_PARTICULAR", "label": "MIA Particular", "type": "proyecto"},
                {"id": "ACME_SA_DE_CV", "label": "ACME S.A. de C.V.", "type": "promovente"},
                {"id": "YUCATAN", "label": "Yucatán", "type": "estado"}
            ],
            "relations": [
                {"src": "MIA_PARTICULAR", "tgt": "ACME_SA_DE_CV", "rel": "PRESENTADO_POR"},
                {"src": "MIA_PARTICULAR", "tgt": "YUCATAN", "rel": "UBICADO_EN"}
            ]
        },
        # Respuesta del loop para finalizar
        {
            "action": "finish",
            "parameters": {},
            "reasoning": "La extracción del grafo concluyó con éxito.",
            "final_summary": "Grafo de conocimiento extraído completamente."
        }
    ]

    with patch("api.rsi_routes._find_document_text", return_value=dummy_text) as mock_find, \
         patch("core.rlm_harness.generate_completion") as mock_gen:
        
        mock_gen.side_effect = mock_responses

        # 1. Enviar petición para iniciar el loop
        payload = {
            "doc_id": "test_document_clave",
            "task": "extraer grafo"
        }
        
        response = client.post("/api/rsi/run", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "QUEUED"
        
        job_id = data["job_id"]

        # 2. Consultar el estado del trabajo (FastAPI TestClient ejecuta BackgroundTasks sincrónicamente)
        status_response = client.get(f"/api/rsi/status/{job_id}")
        assert status_response.status_code == 200
        
        job_data = status_response.json()
        assert job_data["job_id"] == job_id
        assert job_data["status"] == "COMPLETED"
        assert job_data["final_summary"] == "Grafo de conocimiento extraído completamente."
        assert len(job_data["history"]) == 2
        assert job_data["history"][0]["action_selected"] == "graph_extractor"
        assert job_data["history"][1]["action_selected"] == "finish"
        
        # El resultado del sub-agente debe estar en el historial del primer paso
        step_1_result = job_data["history"][0]["result"]
        assert step_1_result["status"] == "SUCCESS"
        assert len(step_1_result["nodes"]) == 3


def test_rsi_run_document_not_found():
    """
    Verifica que el endpoint /run retorna 404 si el documento no existe en disco.
    """
    with patch("api.rsi_routes._find_document_text", side_effect=FileNotFoundError("Archivo no encontrado")):
        payload = {
            "doc_id": "non_existent_doc",
            "task": "extraer grafo"
        }
        response = client.post("/api/rsi/run", json=payload)
        assert response.status_code == 404
        assert "Archivo no encontrado" in response.json()["detail"]


def test_rsi_status_job_not_found():
    """
    Verifica que el endpoint /status/{job_id} retorna 404 para un job inexistente.
    """
    response = client.get("/api/rsi/status/invalid-job-uuid")
    assert response.status_code == 404
    assert "No se encontró" in response.json()["detail"]

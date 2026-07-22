"""
tests/test_model_status_and_optimization.py
=============================================
Pruebas unitarias para la optimización fina, concurrencia y monitoreo de llama-server.
"""

import pytest
import time
import os
import threading
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from api.main import app
from core.llm_client import generate_completion, get_avg_latency_per_token, update_latency_stats

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_latency_stats():
    """Limpia el archivo de stats y las variables globales de latencia antes de cada test."""
    import core.llm_client
    core.llm_client._total_tokens = 0
    core.llm_client._total_time_ms = 0.0
    from core.llm_client import STATS_FILE
    if STATS_FILE.exists():
        try:
            STATS_FILE.unlink()
        except Exception:
            pass
    yield
    # Limpieza final
    core.llm_client._total_tokens = 0
    core.llm_client._total_time_ms = 0.0
    if STATS_FILE.exists():
        try:
            STATS_FILE.unlink()
        except Exception:
            pass


def test_api_status_model_endpoint():
    """
    Verifica que el endpoint /api/status/model responde correctamente.
    """
    # Mockear el estado general del llama-server
    mock_status = {
        "status": "online",
        "model": "gemma-4-e2b-test",
        "details": {"status": "ok"}
    }
    
    # Mockear las estadísticas de docker
    mock_docker_stats = {
        "cpu_pct": 5.4,
        "mem_used_gb": 1.2,
        "mem_limit_gb": 8.0,
        "mem_pct": 15.0
    }

    with patch("api.main.get_llama_status", return_value=mock_status), \
         patch("api.main.get_docker_container_stats", return_value=mock_docker_stats), \
         patch("api.main.os.path.exists", return_value=True):
        
        # Simular una llamada previa para registrar latencia
        update_latency_stats(100, 2500.0) # 25ms por token
        
        response = client.get("/api/status/model")
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "online"
        assert data["model"] == "gemma-4-e2b-test"
        assert data["avg_latency_per_token_ms"] == 25.0
        assert data["container"]["cpu_pct"] == 5.4
        assert data["container"]["mem_pct"] == 15.0


def test_concurrency_lock_llama_server():
    """
    Verifica que el lock de concurrencia funciona correctamente serializando las peticiones.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": '{"veredicto": "FAVORABLE"}',
        "timings": {"predicted_n": 10, "predicted_ms": 250.0}
    }
    
    post_times = []
    
    # Simulamos que cada inferencia toma 0.1 segundos y medimos dentro del post (bajo el Lock)
    def slow_post(*args, **kwargs):
        start = time.time()
        time.sleep(0.1)
        end = time.time()
        post_times.append((start, end))
        return mock_resp

    # Aplicar parches al nivel del test completo para evitar que un hilo deshaga el parche del otro
    with patch("core.llm_client.detect_active_backend", return_value=("llama-server", "gemma-test")), \
         patch("core.llm_client.httpx.post", side_effect=slow_post):
        
        execution_times = []
        
        def worker():
            start = time.time()
            res = generate_completion("test prompt", response_json=True)
            end = time.time()
            execution_times.append((start, end, res))

        # Lanzar dos hilos simultáneamente
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        
        assert len(execution_times) == 2
        # Comprobar que ambas llamadas obtuvieron el mock JSON esperado (no cayeron en fallback)
        assert "veredicto" in execution_times[0][2]
        assert "veredicto" in execution_times[1][2]

        # Comprobar que se ejecutó de forma secuencial en el POST
        assert len(post_times) == 2
        # Ordenamos los post_times por tiempo de inicio
        post_times.sort(key=lambda x: x[0])
        # El inicio de la segunda petición POST debe ser posterior o igual al fin de la primera
        assert post_times[1][0] >= (post_times[0][1] - 0.02)


def test_latency_rolling_average():
    """
    Prueba que la latencia acumulada se calcula correctamente y se trunca tras superar el límite.
    """
    # Inicialmente debe ser 0.0
    assert get_avg_latency_per_token() == 0.0
    
    # Agregar algunos tokens y ms
    update_latency_stats(100, 3000.0) # 30ms/token
    assert get_avg_latency_per_token() == 30.0
    
    # Agregar más
    update_latency_stats(100, 2000.0) # total 200 tokens, 5000.0 ms -> 25ms/token
    assert get_avg_latency_per_token() == 25.0
    
    # Truncado por desbordamiento (>20000)
    update_latency_stats(25000, 500000.0) # total 25200 tokens -> se reduce a 20%
    avg = get_avg_latency_per_token()
    assert 19.0 <= avg <= 21.0

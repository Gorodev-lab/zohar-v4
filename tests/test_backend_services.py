"""
tests/test_backend_services.py
===============================
Suite de pruebas unitarias e integración para los servicios ampliados del backend:
- core/broadcaster.py (EventBroadcaster singleton & multihilo)
- core/llm_enricher.py (BackgroundEnricherWatcher & métricas)
- api/routers/enricher.py (endpoints /api/enricher/*)
"""

import asyncio
import threading
import time
import pytest
from fastapi.testclient import TestClient

from core.broadcaster import broadcaster, EventBroadcaster
from core.llm_enricher import enricher_watcher, BackgroundEnricherWatcher
from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_broadcaster_subscribe_and_broadcast():
    """Verifica que el broadcaster emita eventos correctamente a los suscriptores."""
    loop = asyncio.get_running_loop()
    broadcaster.set_loop(loop)

    q = broadcaster.subscribe()
    assert q in broadcaster._listeners

    def _worker():
        time.sleep(0.05)
        broadcaster.broadcast("test_event", {"msg": "hola_mundo"}, filename="test.pdf")

    t = threading.Thread(target=_worker)
    t.start()

    item = await asyncio.wait_for(q.get(), timeout=2.0)
    t.join()

    assert item["type"] == "test_event"
    assert item["msg"] == "hola_mundo"
    assert item["file"] == "test.pdf"

    broadcaster.unsubscribe(q)
    assert q not in broadcaster._listeners


def test_enricher_watcher_status():
    """Verifica la obtención de métricas y estado del BackgroundEnricherWatcher."""
    status = enricher_watcher.get_status()
    assert isinstance(status, dict)
    assert "running" in status
    assert "poll_interval_sec" in status
    assert "total_processed" in status
    assert "success_count" in status
    assert "pending_projects_count" in status


def test_enricher_endpoints(client):
    """Prueba los endpoints HTTP /api/enricher/status, /start, /stop y /trigger."""
    # 1. Status
    res = client.get("/api/enricher/status")
    assert res.status_code == 200
    data = res.json()
    assert "running" in data

    # 2. Start
    res_start = client.post("/api/enricher/start")
    assert res_start.status_code == 200
    assert res_start.json()["status"] in ("started", "already_running")

    # 3. Stop
    res_stop = client.post("/api/enricher/stop")
    assert res_stop.status_code == 200
    assert res_stop.json()["status"] in ("stopped", "not_running")

    # 4. Trigger
    res_trig = client.post("/api/enricher/trigger?limit=1")
    assert res_trig.status_code == 200
    trig_data = res_trig.json()
    assert trig_data["status"] == "success"
    assert "data" in trig_data

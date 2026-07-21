"""
tests/test_classifier.py
Pruebas unitarias para el clasificador heurístico determinístico core/classifier.py.
"""

from pathlib import Path
from core.classifier import classify_item, DocumentClassifier
from fastapi.testclient import TestClient
import api.main as main_module

def test_classify_semarnat_sinat_keys():
    # 1. Clave 21PU2025H0155 (Sector 21 Hidrocarburos, Puebla, 2025, MIA Particular, 0155)
    res1 = classify_item("21PU2025H0155")
    assert res1["is_valid_sinat"] is True
    assert res1["source"] == "SEMARNAT"
    assert res1["sector_code"] == "21"
    assert res1["estado_code"] == "PU"
    assert res1["estado_name"] == "Puebla"
    assert res1["year"] == 2025
    assert res1["tipo_code"] == "H"
    assert res1["tipo_name"] == "MIA Particular"
    assert res1["sequence"] == "0155"

    # 2. Clave 10DU2026X0015 con sufijo .estudio.01.md
    res2 = classify_item("10DU2026X0015.estudio.01.md")
    assert res2["is_valid_sinat"] is True
    assert res2["source"] == "SEMARNAT"
    assert res2["estado_code"] == "DU" or res2["estado_code"] == "DU"
    assert res2["year"] == 2026
    assert res2["tipo_code"] == "X"
    assert res2["tipo_name"] == "Trámite Sector Hidrocarburos (X)"
    assert res2["doc_category"] == "estudio"

    # 3. Clave 03BS2026H0015 (Baja California Sur)
    res3 = classify_item("03BS2026H0015")
    assert res3["is_valid_sinat"] is True
    assert res3["estado_code"] == "BS"
    assert res3["estado_name"] == "Baja California Sur"

def test_classify_asea_gazettes():
    # 1. Gaceta ASEA
    res1 = classify_item("ASEA_GACETA_01-2026.pdf")
    assert res1["is_valid_sinat"] is False
    assert res1["source"] == "ASEA"
    assert res1["year"] == 2026
    assert res1["doc_category"] == "gaceta"

def test_classify_endpoint():
    client = TestClient(main_module.app)
    resp = client.get("/api/classifier/classify?input_string=03BS2026H0015")
    assert resp.status_code == 200
    data = resp.json()
    assert data["clave"] == "03BS2026H0015"
    assert data["estado_name"] == "Baja California Sur"

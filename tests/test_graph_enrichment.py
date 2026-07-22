"""
tests/test_graph_enrichment.py
Pruebas de enriquecimiento y construcción de grafos para Zohar v4.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.graph_builder import (
    parse_semarnat_key,
    scan_corpus,
    load_db_metadata,
    load_inference_cache,
    load_csv_metadata,
    enrich_projects_metadata,
    build_graph,
    to_compact_graph,
    build_full_graph
)


def test_parse_semarnat_key():
    """Valida la extracción estructurada de la clave SINAT."""
    parsed = parse_semarnat_key("21PU2025H0155.pdf")
    assert parsed["valid"] is True
    assert parsed["clave"] == "21PU2025H0155"
    assert parsed["estado"] == "PU"
    assert parsed["estado_nombre"] == "Puebla"
    assert parsed["year"] == 2025
    assert parsed["tipo"] == "H"
    assert parsed["tipo_nombre"] == "MIA Particular"


@patch("core.graph_builder.load_db_metadata")
@patch("core.graph_builder.load_inference_cache")
@patch("core.graph_builder.load_csv_metadata")
def test_enrich_projects_metadata(mock_csv, mock_cache, mock_db):
    """Prueba la combinación de metadatos desde DB, Cache de Inferencia y CSV."""
    mock_db.return_value = {
        "01AG2025X0047": {
            "project_name": "Parque Solar Aguascalientes",
            "promovente": "SolarMX S.A.",
            "municipio": None,
            "veredicto": None
        }
    }
    mock_cache.return_value = {
        "01AG2025X0047": {
            "project_name": "Parque Solar Aguascalientes II",
            "promovente": "SolarMX S.A.",
            "municipio": "El Llano",
            "veredicto": "FAVORABLE"
        }
    }
    mock_csv.return_value = {
        "01AG2025X0047": {
            "project_name": "Parque Solar",
            "promovente": "SolarMX",
            "municipio": "El Llano Municipio",
            "veredicto": None
        }
    }

    projects = [
        {
            "clave": "01AG2025X0047",
            "valid": True,
            "estado": "AG",
            "year": 2025,
            "tipo": "X",
            "sector": "01"
        }
    ]

    enriched = enrich_projects_metadata(projects)
    p = enriched[0]

    # Prioridad: Cache de inferencia tiene precedencia
    assert p["project_name"] == "Parque Solar Aguascalientes II"
    assert p["promovente"] == "SolarMX S.A."
    assert p["municipio"] == "El Llano"
    assert p["veredicto"] == "FAVORABLE"


def test_build_graph_with_new_nodes():
    """Valida la inserción de promovente y municipio en el grafo."""
    projects = [
        {
            "clave": "21PU2025H0155",
            "valid": True,
            "estado": "PU",
            "estado_nombre": "Puebla",
            "year": 2025,
            "tipo": "H",
            "tipo_nombre": "MIA Particular",
            "sector": "21",
            "project_name": "Residencial Bosques",
            "promovente": "Desarrollos Bosque S.A.",
            "municipio": "Tehuacán",
            "veredicto": "CONDICIONADO"
        }
    ]

    graph = build_graph(projects)
    nodes = {n["id"]: n for n in graph["nodes"]}

    # Verificar nodos creados
    assert "21PU2025H0155" in nodes
    assert nodes["21PU2025H0155"]["type"] == "proyecto"
    assert nodes["21PU2025H0155"]["veredicto"] == "CONDICIONADO"
    assert nodes["21PU2025H0155"]["project_name"] == "Residencial Bosques"

    assert "prom_DESARROLLOS BOSQUE S.A." in nodes
    assert nodes["prom_DESARROLLOS BOSQUE S.A."]["type"] == "promovente"
    assert nodes["prom_DESARROLLOS BOSQUE S.A."]["label"] == "Desarrollos Bosque S.A."

    assert "muni_PU_TEHUACÁN" in nodes
    assert nodes["muni_PU_TEHUACÁN"]["type"] == "municipio"
    assert nodes["muni_PU_TEHUACÁN"]["label"] == "Tehuacán"

    # Verificar relaciones
    relations = graph["relations"]
    rel_pairs = {(r["src"], r["tgt"], r["rel"]) for r in relations}

    assert ("21PU2025H0155", "muni_PU_TEHUACÁN", "UBICADO_EN") in rel_pairs
    assert ("muni_PU_TEHUACÁN", "estado_PU", "PERTENECE_A") in rel_pairs
    assert ("21PU2025H0155", "prom_DESARROLLOS BOSQUE S.A.", "PROMOVIDO_POR") in rel_pairs


def test_to_compact_graph():
    """Valida la salida compacta optimizada para D3.js."""
    projects = [
        {
            "clave": "21PU2025H0155",
            "valid": True,
            "estado": "PU",
            "year": 2025,
            "tipo": "H",
            "sector": "21",
            "project_name": "Proyecto Bosques",
            "promovente": "Bosques SA",
            "municipio": "Puebla",
            "veredicto": "FAVORABLE"
        }
    ]

    graph = build_graph(projects)
    compact = to_compact_graph(graph)

    # Verificar esquema ampliado
    assert compact["schema"]["nodes"] == ["i", "t", "l", "st", "yr", "deg", "com", "name", "veredicto"]
    
    # Verificar que el nodo de proyecto tiene el nombre real y veredicto en su array compacto
    proj_node = next(n for n in compact["nodes"] if n[0] == "21PU2025H0155")
    assert proj_node[7] == "Proyecto Bosques"
    assert proj_node[8] == "FAVORABLE"

    # Verificar métricas
    assert compact["metrics"]["n_projects"] == 1
    assert compact["metrics"]["n_nodes"] > 0
    assert compact["metrics"]["n_links"] > 0

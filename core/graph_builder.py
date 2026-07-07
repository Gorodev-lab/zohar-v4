"""
core/graph_builder.py
Knowledge Graph de proyectos SEMARNAT para visualización con D3.js.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Clave SINAT: formato  XX[ESTADO]YYYY[TIPO][SECUENCIA]
# Ejemplo: 21PU2025H0155  → estado=PU, año=2025, tipo=H, seq=0155
# ---------------------------------------------------------------------------

_CLAVE_RE = re.compile(
    r"^(?P<sector>\d{2})"
    r"(?P<estado>[A-Z]{2})"
    r"(?P<year>\d{4})"
    r"(?P<tipo>[A-Z])"
    r"(?P<seq>\d{4})$"
)

ESTADO_NOMBRES = {
    "AG": "Aguascalientes", "BC": "Baja California", "BS": "Baja California Sur",
    "CM": "Campeche", "CS": "Chiapas", "CH": "Chihuahua", "CO": "Coahuila",
    "CL": "Colima", "DF": "Ciudad de México", "DG": "Durango", "GT": "Guanajuato",
    "GR": "Guerrero", "HG": "Hidalgo", "JL": "Jalisco", "ME": "Estado de México",
    "MI": "Michoacán", "MO": "Morelos", "NA": "Nayarit", "NL": "Nuevo León",
    "OA": "Oaxaca", "PU": "Puebla", "QT": "Querétaro", "QR": "Quintana Roo",
    "SL": "San Luis Potosí", "SI": "Sinaloa", "SO": "Sonora", "TB": "Tabasco",
    "TM": "Tamaulipas", "TL": "Tlaxcala", "VE": "Veracruz", "YU": "Yucatán",
    "ZA": "Zacatecas", "MG": "Nacional/Marino", "MP": "Múltiple",
}

TIPO_MIA = {
    "H": "MIA Particular", "G": "MIA Regional",
    "I": "Informe Preventivo", "T": "Cambio de Uso de Suelo",
    "D": "Estudio de Riesgo",
}

# Colores del grafo por tipo de nodo
NODE_COLORS = {
    "proyecto":   "#FFB000",
    "estado":     "#27AE60",
    "tipo_mia":   "#9B59B6",
    "año":        "#3498DB",
    "sector":     "#E67E22",
    "promovente": "#E84393",
    "municipio":  "#5DADE2",
}

REL_MAP = {
    0: "UBICADO_EN",
    1: "ES_TIPO",
    2: "DEL_AÑO",
    3: "DEL_SECTOR",
}


def parse_semarnat_key(filename: str) -> dict:
    """
    Parsea una clave SINAT del nombre de archivo.
    Retorna dict con campos extraídos o vacío si no coincide.
    """
    stem = Path(filename).stem
    # Intentar extraer clave del nombre
    parts = stem.split(".")
    clave_candidate = parts[0] if parts else stem

    m = _CLAVE_RE.match(clave_candidate.upper())
    if not m:
        return {"clave": clave_candidate, "valid": False}

    estado_code = m.group("estado")
    tipo_code = m.group("tipo")

    return {
        "clave":     clave_candidate.upper(),
        "sector":    m.group("sector"),
        "estado":    estado_code,
        "estado_nombre": ESTADO_NOMBRES.get(estado_code, estado_code),
        "year":      int(m.group("year")),
        "tipo":      tipo_code,
        "tipo_nombre": TIPO_MIA.get(tipo_code, tipo_code),
        "seq":       m.group("seq"),
        "valid":     True,
    }


def scan_corpus(base_path: Path) -> list[dict]:
    """
    Escanea el corpus de PDFs en base_path.
    Retorna lista de proyectos con metadata extraída.
    """
    projects = []
    base_path = Path(base_path)

    if not base_path.exists():
        return []

    seen_claves = set()
    for pdf in base_path.rglob("*.pdf"):
        parsed = parse_semarnat_key(pdf.name)
        clave = parsed.get("clave", "")
        if clave in seen_claves:
            continue
        seen_claves.add(clave)

        entry = {
            **parsed,
            "filename": pdf.name,
            "path": str(pdf),
            "size_bytes": pdf.stat().st_size,
            "subfolder": pdf.parent.name,
        }
        projects.append(entry)

    return projects


def build_graph(projects: list[dict]) -> dict:
    """
    Construye el grafo de conocimiento a partir de proyectos.
    Retorna dict con nodos y relaciones.
    """
    nodes: dict[str, dict] = {}
    relations: list[dict] = []

    def add_node(node_id: str, label: str, node_type: str, **attrs) -> str:
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "label": label,
                "type": node_type,
                "color": NODE_COLORS.get(node_type, "#AAAAAA"),
                "degree": 0,
                **attrs,
            }
        return node_id

    for p in projects:
        if not p.get("valid"):
            continue

        clave = p["clave"]
        proj_id = add_node(clave, clave, "proyecto", year=p.get("year"))

        # Nodo estado
        estado_id = f"estado_{p['estado']}"
        add_node(estado_id, p.get("estado_nombre", p["estado"]), "estado")
        relations.append({"src": proj_id, "tgt": estado_id, "rel": "UBICADO_EN"})
        nodes[proj_id]["degree"] += 1
        nodes[estado_id]["degree"] += 1

        # Nodo tipo MIA
        tipo_id = f"tipo_{p['tipo']}"
        add_node(tipo_id, p.get("tipo_nombre", p["tipo"]), "tipo_mia")
        relations.append({"src": proj_id, "tgt": tipo_id, "rel": "ES_TIPO"})
        nodes[proj_id]["degree"] += 1
        nodes[tipo_id]["degree"] += 1

        # Nodo año
        year_id = f"anio_{p['year']}"
        add_node(year_id, str(p["year"]), "año")
        relations.append({"src": proj_id, "tgt": year_id, "rel": "DEL_AÑO"})
        nodes[proj_id]["degree"] += 1
        nodes[year_id]["degree"] += 1

        # Nodo sector
        sector_id = f"sector_{p['sector']}"
        add_node(sector_id, f"Sector {p['sector']}", "sector")
        relations.append({"src": proj_id, "tgt": sector_id, "rel": "DEL_SECTOR"})
        nodes[proj_id]["degree"] += 1
        nodes[sector_id]["degree"] += 1

    return {
        "nodes": list(nodes.values()),
        "relations": relations,
        "n_projects": sum(1 for n in nodes.values() if n["type"] == "proyecto"),
    }


def to_compact_graph(graph: dict) -> dict:
    """
    Convierte el grafo a formato compacto para D3.js.

    Schema de salida fijo:
    {
        "schema": {
            "nodes": ["i","t","l","st","yr","deg","com"],
            "rel_map": {0: "UBICADO_EN", 1: "ES_TIPO", ...}
        },
        "nodes": [[i, t, l, st, yr, deg, com], ...],
        "links": [[src_idx, tgt_idx, rel_idx], ...],
        "metrics": {...}
    }
    """
    nodes = graph["nodes"]
    relations = graph["relations"]

    # Índice de nodos
    node_idx = {n["id"]: i for i, n in enumerate(nodes)}

    # Detectar comunidades simples (por tipo de nodo como proxy)
    type_to_com = {t: i for i, t in enumerate(NODE_COLORS.keys())}

    compact_nodes = []
    for n in nodes:
        com = type_to_com.get(n["type"], 0)
        compact_nodes.append([
            n["id"],          # i: id
            n["type"],        # t: type
            n["label"],       # l: label
            n["color"],       # st: style/color
            n.get("year"),    # yr: year
            n.get("degree", 0),  # deg: degree
            com,              # com: community
        ])

    # Mapa inverso de relaciones
    rel_names = list(dict.fromkeys(r["rel"] for r in relations))
    rel_to_idx = {r: i for i, r in enumerate(rel_names)}
    rel_map = {i: r for r, i in rel_to_idx.items()}

    compact_links = []
    for r in relations:
        src_i = node_idx.get(r["src"])
        tgt_i = node_idx.get(r["tgt"])
        rel_i = rel_to_idx.get(r["rel"], 0)
        if src_i is not None and tgt_i is not None:
            compact_links.append([src_i, tgt_i, rel_i])

    # Métricas básicas
    degree_sum = sum(n.get("degree", 0) for n in nodes)
    metrics = {
        "n_nodes": len(nodes),
        "n_links": len(compact_links),
        "n_projects": graph.get("n_projects", 0),
        "avg_degree": round(degree_sum / max(len(nodes), 1), 2),
    }

    return {
        "schema": {
            "nodes": ["i", "t", "l", "st", "yr", "deg", "com"],
            "rel_map": rel_map,
        },
        "nodes": compact_nodes,
        "links": compact_links,
        "metrics": metrics,
    }


def build_full_graph(base_path: Path) -> dict:
    """Pipeline completo: scan → build → compact."""
    projects = scan_corpus(base_path)
    graph = build_graph(projects)
    return to_compact_graph(graph)

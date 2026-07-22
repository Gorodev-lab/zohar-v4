"""
core/graph_builder.py
Knowledge Graph de proyectos SEMARNAT para visualización con D3.js.
"""

from __future__ import annotations

import json
import os
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
    "X": "Trámite Sector Hidrocarburos (X)",
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


def load_db_metadata() -> dict[str, dict]:
    """Carga metadatos enriquecidos de todos los proyectos de la base de datos."""
    metadata = {}
    try:
        import sqlalchemy as sa
        from core.config import DATABASE_URL
        engine = sa.create_engine(DATABASE_URL)
        with engine.connect() as conn:
            # 1. Intentar con la tabla proyectos / promoventes primero
            try:
                query = sa.text("""
                    SELECT p.clave, p.nombre, pr.nombre as promovente, p.estado
                    FROM proyectos p
                    LEFT JOIN promoventes pr ON p.promovente_id = pr.id
                """)
                rows = conn.execute(query).fetchall()
                for row in rows:
                    metadata[row[0].upper()] = {
                        "project_name": row[1],
                        "promovente": row[2],
                        "municipio": None,
                        "veredicto": None
                    }
            except Exception:
                pass

            # 2. Intentar con public.semarnat_projects y public.project_evaluations
            try:
                query = sa.text("""
                    SELECT p.clave, p.project_name, p.promovente, p.state, e.veredicto
                    FROM public.semarnat_projects p
                    LEFT JOIN public.project_evaluations e ON p.clave = e.clave
                """)
                rows = conn.execute(query).fetchall()
                for row in rows:
                    clave_upper = row[0].upper()
                    entry = metadata.setdefault(clave_upper, {})
                    if row[1]:
                        entry["project_name"] = row[1]
                    if row[2]:
                        entry["promovente"] = row[2]
                    if row[3]:
                        entry["municipio"] = row[3]
                    if row[4]:
                        entry["veredicto"] = row[4]
            except Exception:
                pass
    except Exception:
        pass
    return metadata


def load_inference_cache() -> dict[str, dict]:
    """Carga veredictos y otros datos de la cache de inferencia local."""
    from core.config import DATA_DIR
    cache_dir = DATA_DIR / "inference_cache"
    cache = {}
    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                clave = data.get("clave") or f.stem
                if clave:
                    cache[clave.upper()] = {
                        "veredicto": data.get("veredicto"),
                        "project_name": data.get("project_name") or data.get("nombre_proyecto"),
                        "promovente": data.get("promovente"),
                        "municipio": data.get("municipio")
                    }
            except Exception:
                pass
    return cache


def load_csv_metadata() -> dict[str, dict]:
    """Carga metadatos desde todos los archivos claves_*.csv en data/."""
    import csv
    from core.config import DATA_DIR
    metadata = {}
    for csv_file in DATA_DIR.glob("claves_*.csv"):
        try:
            with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if not row or len(row) < 3:
                        continue
                    clave = row[0].strip().upper()
                    if len(row) >= 7:
                        proj_name = row[4].strip()
                        loc = row[5].strip()
                        prom = row[6].strip()
                    else:
                        proj_name = row[3].strip() if len(row) > 3 else f"Proyecto {clave}"
                        loc = row[4].strip() if len(row) > 4 else ""
                        prom = row[5].strip() if len(row) > 5 else "Desconocido"

                    metadata[clave] = {
                        "project_name": proj_name,
                        "promovente": prom,
                        "municipio": loc,
                        "veredicto": None
                    }
        except Exception:
            pass
    return metadata


def enrich_projects_metadata(projects: list[dict]) -> list[dict]:
    """Enriquece una lista de proyectos escaneados combinando DB, Cache de Inferencia y CSV."""
    db_meta = load_db_metadata()
    cache_meta = load_inference_cache()
    csv_meta = load_csv_metadata()

    for p in projects:
        clave = p.get("clave", "").upper()
        if not clave:
            continue

        m_cache = cache_meta.get(clave, {})
        m_db = db_meta.get(clave, {})
        m_csv = csv_meta.get(clave, {})

        proj_name = m_cache.get("project_name") or m_db.get("project_name") or m_csv.get("project_name") or p.get("project_name") or f"Proyecto {clave}"
        promovente = m_cache.get("promovente") or m_db.get("promovente") or m_csv.get("promovente") or p.get("promovente") or "Desconocido"
        municipio = m_cache.get("municipio") or m_db.get("municipio") or m_csv.get("municipio") or p.get("municipio") or None
        veredicto = m_cache.get("veredicto") or m_db.get("veredicto") or None

        p["project_name"] = proj_name
        p["promovente"] = promovente
        p["municipio"] = municipio
        p["veredicto"] = veredicto

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
        proj_id = add_node(
            clave, 
            clave, 
            "proyecto", 
            year=p.get("year"),
            project_name=p.get("project_name", f"Proyecto {clave}"),
            veredicto=p.get("veredicto")
        )

        # Nodo estado
        estado_code = p["estado"]
        estado_id = f"estado_{estado_code}"
        add_node(estado_id, p.get("estado_nombre", estado_code), "estado")
        
        # Enlace territorial
        municipio_name = p.get("municipio")
        if municipio_name and municipio_name.strip() and municipio_name.lower() not in ("desconocido", "desconocida", "", "none"):
            muni_clean = municipio_name.strip()
            muni_id = f"muni_{estado_code}_{muni_clean.upper()}"
            add_node(muni_id, muni_clean, "municipio")
            
            relations.append({"src": proj_id, "tgt": muni_id, "rel": "UBICADO_EN"})
            relations.append({"src": muni_id, "tgt": estado_id, "rel": "PERTENECE_A"})
            nodes[proj_id]["degree"] += 1
            nodes[muni_id]["degree"] += 2
            nodes[estado_id]["degree"] += 1
        else:
            relations.append({"src": proj_id, "tgt": estado_id, "rel": "UBICADO_EN"})
            nodes[proj_id]["degree"] += 1
            nodes[estado_id]["degree"] += 1

        # Nodo promovente
        prom_name = p.get("promovente")
        if prom_name and prom_name.strip() and prom_name.lower() not in ("desconocido", "", "none"):
            prom_clean = prom_name.strip()
            prom_id = f"prom_{prom_clean.upper()}"
            add_node(prom_id, prom_clean, "promovente")
            relations.append({"src": proj_id, "tgt": prom_id, "rel": "PROMOVIDO_POR"})
            nodes[proj_id]["degree"] += 1
            nodes[prom_id]["degree"] += 1

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
            n["id"],             # i: id
            n["type"],           # t: type
            n["label"],          # l: label
            n["color"],          # st: style/color
            n.get("year"),       # yr: year
            n.get("degree", 0),  # deg: degree
            com,                 # com: community
            n.get("project_name"), # name: project_name
            n.get("veredicto"),  # veredicto: veredicto
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
            "nodes": ["i", "t", "l", "st", "yr", "deg", "com", "name", "veredicto"],
            "rel_map": rel_map,
        },
        "nodes": compact_nodes,
        "links": compact_links,
        "metrics": metrics,
    }


def build_full_graph(base_path: Path) -> dict:
    """Pipeline completo: scan → enrich → build → compact."""
    projects = scan_corpus(base_path)
    projects = enrich_projects_metadata(projects)
    graph = build_graph(projects)
    return to_compact_graph(graph)


def invalidate_graph_cache() -> bool:
    """
    Invalida el caché de disco (data/graph_cache.json) y el caché de Redis (zohar:graph:compact).
    """
    from core.config import DATA_DIR
    cache_path = DATA_DIR / "graph_cache.json"
    if cache_path.exists():
        try:
            cache_path.unlink()
        except Exception:
            pass

    try:
        import redis
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        r = redis.Redis(host=redis_host, port=6379, db=0)
        r.delete("zohar:graph:compact")
    except Exception:
        pass

    return True



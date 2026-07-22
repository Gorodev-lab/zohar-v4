from fastapi import APIRouter
from sqlalchemy import create_engine, text
from core.dw_pipeline import DB_URL
import logging

router = APIRouter()
engine = create_engine(DB_URL)

@router.get("/data")
def get_graph_data():
    try:
        with engine.connect() as conn:
            # Extraer nodos
            nodes_records = conn.execute(text("SELECT id, label, type, community, degree FROM public.kg_nodes")).mappings().all()
            nodes = [dict(r) for r in nodes_records]
            
            # Extraer aristas
            edges_records = conn.execute(text("SELECT source, target, relationship as type, weight FROM public.kg_edges")).mappings().all()
            links = [{"source": r["source"], "target": r["target"], "type": r["type"], "weight": r["weight"]} for r in edges_records]
            
        return {"nodes": nodes, "links": links}
    except Exception as e:
        logging.error(f"Error fetching graph data: {e}")
        return {"nodes": [], "links": []}

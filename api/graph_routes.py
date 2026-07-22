from fastapi import APIRouter, Request
import asyncpg

router = APIRouter()

@router.get("/data")
async def get_graph_data(request: Request):
    # Asumimos que tienes el pool inyectado en request.app.state o importable de core
    pool: asyncpg.Pool = request.app.state.db_pool 
    
    async with pool.acquire() as conn:
        # Extraer nodos
        nodes_records = await conn.fetch("SELECT id, label, type, community, degree FROM public.kg_nodes")
        nodes = [dict(r) for r in nodes_records]
        
        # Extraer aristas (formateadas como 'links' para D3.js)
        edges_records = await conn.fetch("SELECT source, target, relationship as type, weight FROM public.kg_edges")
        links = [{"source": r["source"], "target": r["target"], "type": r["type"], "weight": r["weight"]} for r in edges_records]
        
    return {"nodes": nodes, "links": links}

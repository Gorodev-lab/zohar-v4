from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import logging
import json
from .graph_service import load_graphify_graph

router = APIRouter()

@router.get("/graph")
def get_graph(format: str = "compact"):
    try:
        graph_data = load_graphify_graph()
        if format == "compact":
            return JSONResponse(content=graph_data, media_type="application/json")
        return graph_data
    except Exception as e:
        logging.error(f"Error fetching graph data: {e}")
        return JSONResponse(content={"nodes": [], "links": []}, status_code=500)

import json
import os

GRAPHIFY_PATH = "graphify-out/graph_zohar_format.json"

def load_graphify_graph():
    if os.path.exists(GRAPHIFY_PATH):
        with open(GRAPHIFY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"nodes": [], "links": []}

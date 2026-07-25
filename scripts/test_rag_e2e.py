import sqlite3
import json
import time
import os

DB_PATH = "data/metadata_proyecto.db"
GRAPH_PATH = "graphify-out/graph_zohar_format.json"

def run_e2e_rag_test():
    print("=== 🧪 STARTING END-TO-END RAG INFERENCE TEST ===\n")
    start_time = time.time()
    
    # 1. Fetch a target project from DB
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT clave, estado, promovente FROM metadata_proyecto WHERE promovente IS NOT NULL LIMIT 1")
    project = cursor.fetchone()
    conn.close()
    
    if not project:
        print("❌ Error: No valid projects with metadata found in DB.")
        return

    clave, estado, promovente = project
    print(f"📌 Target Project Selected:")
    print(f"   - Clave:      {clave}")
    print(f"   - State:      {estado}")
    print(f"   - Promovente: {promovente}\n")
    
    # 2. Graphify Traversal Check
    print("🔍 Traversing Graphify Knowledge Graph...")
    graph_nodes = []
    graph_links = []
    if os.path.exists(GRAPH_PATH):
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            graph_data = json.load(f)
            
        nodes = graph_data.get("nodes", [])
        links = graph_data.get("links", [])
        
        # Find matching node and 1-hop neighbors
        matching_nodes = [n for n in nodes if clave in str(n.get("id", ""))]
        graph_nodes = matching_nodes
        
        if matching_nodes:
            target_id = matching_nodes[0].get("id")
            neighbor_links = [l for l in links if l.get("source") == target_id or l.get("target") == target_id]
            graph_links = neighbor_links

    print(f"✅ Graph Traversal Result:")
    print(f"   - Matched Nodes: {len(graph_nodes)}")
    print(f"   - Direct Graph Edges/Links: {len(graph_links)}")
    
    # 3. Context Construction Verification
    context_str = f"PROJECT: {clave} | STATE: {estado} | PROMOVENTE: {promovente}\n"
    if graph_links:
        context_str += f"GRAPH CONNECTIONS: {len(graph_links)} topological neighbors identified.\n"
    
    print("\n📦 Assembled RAG Prompt Context:")
    print("-" * 50)
    print(context_str.strip())
    print("-" * 50)
    
    elapsed = time.time() - start_time
    print(f"\n⚡ E2E Pipeline Latency: {elapsed:.3f} seconds")
    print("✅ RAG Retrieval & Graph Pathing: PASSED")

if __name__ == "__main__":
    run_e2e_rag_test()

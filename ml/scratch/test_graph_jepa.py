#!/usr/bin/env python3
import os
import sys
import time
import requests
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def main():
    print("====================================================")
    print("      LOGR Knowledge Graph Similarity Edge Test")
    print("====================================================")

    # 1. Load env variables
    load_dotenv(".env.local")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("[Error] Supabase credentials missing in .env.local")
        sys.exit(1)

    # 2. Insert two similar anomalies using Supabase REST API
    # We map them to vessels 4 and 5, which are present in the top 15 graph nodes
    # (MMSI 345160036 and 345020003 respectively)
    anomaly_a_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    anomaly_b_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    url = f"{supabase_url}/rest/v1/traffic_anomalies"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    # Embeddings of 128 dimensions, identical to guarantee high similarity
    embedding_vector = [1.0] + [0.0] * 127
    embedding_str = "[" + ",".join(map(str, embedding_vector)) + "]"

    test_anomalies = [
        {
            "id": anomaly_a_id,
            "vessel_id": 2,
            "anomaly_type": "Test Anomaly A",
            "start_time": "2026-06-26T00:00:00+00:00",
            "description": "Test anomaly for vessel 2.",
            "jepa_behavior_embedding": embedding_str
        },
        {
            "id": anomaly_b_id,
            "vessel_id": 8,
            "anomaly_type": "Test Anomaly B",
            "start_time": "2026-06-26T00:00:00+00:00",
            "description": "Test anomaly for vessel 8.",
            "jepa_behavior_embedding": embedding_str
        }
    ]

    print("[Test] Inserting temporary test anomalies to Supabase...")
    # Clean up first in case they were left from a previous run
    requests.delete(f"{url}?id=in.({anomaly_a_id},{anomaly_b_id})", headers=headers)

    res = requests.post(url, json=test_anomalies, headers=headers)
    if res.status_code not in (200, 201):
        print(f"[Error] Failed to insert anomalies: {res.status_code} - {res.text}")
        sys.exit(1)
    print("=> Anomalies inserted successfully.")

    # 3. Check if Next.js dev server is running, or start it
    server_process = None
    api_url = "http://localhost:3000/api/graph-data"
    
    print("[Test] Checking if Next.js server is running on localhost:3000...")
    try:
        r = requests.get(api_url, timeout=2)
        print("=> Next.js server is already running.")
    except requests.exceptions.RequestException:
        print("=> Server not running. Starting Next.js dev server on port 3000...")
        server_process = subprocess.Popen(
            ["npm", "run", "dev"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid
        )
        # Wait for the server to be ready
        retries = 30
        connected = False
        while retries > 0:
            try:
                time.sleep(1)
                r = requests.get(api_url, timeout=2)
                if r.status_code in (200, 404): # 404 is also okay (means route exists but empty graph)
                    connected = True
                    break
            except requests.exceptions.RequestException:
                retries -= 1
        
        if not connected:
            print("[Error] Failed to start Next.js server. Terminating...")
            import signal
            os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
            sys.exit(1)
        print("=> Next.js server is ready.")

    # 4. Request graph data and verify similarity edge
    print("[Test] Fetching graph data from /api/graph-data...")
    try:
        r = requests.get(api_url)
        assert r.status_code == 200, f"Unexpected response status: {r.status_code}"
        
        data = r.json()
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        
        # Verify anomaly nodes exist
        node_ids = {n["id"] for n in nodes}
        anomaly_a_node = f"ta_{anomaly_a_id}"
        anomaly_b_node = f"ta_{anomaly_b_id}"
        
        assert anomaly_a_node in node_ids, f"Node {anomaly_a_node} not found in graph response."
        assert anomaly_b_node in node_ids, f"Node {anomaly_b_node} not found in graph response."
        print(f"=> Found Anomaly nodes in graph: {anomaly_a_node}, {anomaly_b_node}")
        
        # Verify GENERATED_ANOMALY edges exist
        vessel_a_node = "vi_636093089"
        vessel_b_node = "vi_354212000"
        
        gen_a_exists = False
        gen_b_exists = False
        sim_edge_exists = False
        similarity_score = 0.0

        for edge in edges:
            if edge.get("label") == "GENERATED_ANOMALY":
                if edge.get("from") == vessel_a_node and edge.get("to") == anomaly_a_node:
                    gen_a_exists = True
                if edge.get("from") == vessel_b_node and edge.get("to") == anomaly_b_node:
                    gen_b_exists = True
            elif edge.get("label") == "SIMILAR_BEHAVIOR":
                if (edge.get("from") == anomaly_a_node and edge.get("to") == anomaly_b_node) or \
                   (edge.get("from") == anomaly_b_node and edge.get("to") == anomaly_a_node):
                    sim_edge_exists = True
                    similarity_score = edge.get("similarity", 0.0)

        assert gen_a_exists, f"GENERATED_ANOMALY edge from {vessel_a_node} to {anomaly_a_node} not found."
        assert gen_b_exists, f"GENERATED_ANOMALY edge from {vessel_b_node} to {anomaly_b_node} not found."
        print("=> GENERATED_ANOMALY edges verified successfully.")

        assert sim_edge_exists, "SIMILAR_BEHAVIOR edge between the two anomalies not found."
        assert similarity_score >= 0.85, f"SIMILAR_BEHAVIOR edge found, but similarity score {similarity_score} is below 0.85."
        print(f"=> SIMILAR_BEHAVIOR edge verified successfully with cosine similarity: {similarity_score:.4f}")

        print("\n====================================================")
        print("         ALL GRAPH INTEGRATION TESTS PASSED!")
        print("====================================================")

    except AssertionError as ae:
        print(f"\n[Test Failure] Assertion failed: {ae}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[Test Failure] Error occurred: {e}")
        sys.exit(1)
    finally:
        # 5. Cleanup temporary anomalies
        print("[Cleanup] Removing temporary anomalies from Supabase...")
        requests.delete(f"{url}?id=in.({anomaly_a_id},{anomaly_b_id})", headers=headers)
        
        # Terminate server if started by subprocess
        if server_process:
            print("[Cleanup] Terminating Next.js dev server subprocess...")
            import signal
            os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
            server_process.wait()

if __name__ == "__main__":
    main()

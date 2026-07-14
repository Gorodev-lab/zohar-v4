#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
for p in [Path('.'), Path('..'), Path(__file__).parent.parent]:
    for env_file in ['.env.local', '.env']:
        env_path = p / env_file
        if env_path.exists():
            load_dotenv(env_path)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

def run_graphify_cli(temp_dir: Path) -> dict:
    """Runs the graphify CLI on the temp_corpus and returns the resulting graph dict."""
    # Find the graphify binary in the virtual environment if present
    venv_bin = Path(sys.executable).parent
    graphify_bin = venv_bin / "graphify"
    if not graphify_bin.exists():
        graphify_bin = Path("graphify")  # Fallback to system PATH

    print(f"[Graphify CLI] Executing: {graphify_bin} {temp_dir} --no-viz")
    env = os.environ.copy()
    
    # We run the command and capture output
    result = subprocess.run(
        [str(graphify_bin), str(temp_dir), "--no-viz"],
        env=env,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"graphify CLI failed (code {result.returncode}):\n{result.stderr}\n{result.stdout}")
        
    print("[Graphify CLI] Completed successfully.")
    output_json = temp_dir / "graphify-out" / "graph.json"
    if not output_json.exists():
        raise FileNotFoundError(f"graphify-out/graph.json not found in {temp_dir}")
        
    with open(output_json, "r", encoding="utf-8") as f:
        return json.load(f)

def generate_programmatic_graph(conn) -> dict:
    """Programmatic fallback: Queries all tables and builds the complete knowledge graph JSON."""
    print("[Programmatic Graph] Querying database tables for full graph construction...")
    
    # Query vessels
    vessels = conn.execute(text(
        "SELECT vessel_id, mmsi, shipname, flag, vessel_type, risk_score, risk_level, last_seen "
        "FROM public.gfw_vessels"
    )).mappings().all()
    
    # Query occurrences
    occurrences = conn.execute(text(
        "SELECT id, species, scientific_name, event_date, latitude, longitude, h3_index, taxa_group "
        "FROM public.obis_occurrences"
    )).mappings().all()
    
    # Query permits
    permits = conn.execute(text(
        "SELECT id, year, rnp, vessel_registration_number, species_permit, fishing_gear_number, fishing_gear_type "
        "FROM public.conapesca_permits"
    )).mappings().all()
    
    # Query encounters, joining fishing effort for coordinates
    encounters = conn.execute(text("""
        SELECT 
            e.effort_id,
            e.vessel_id,
            e.vessel_name,
            e.vessel_flag,
            e.vessel_type,
            e.vessel_risk_score,
            e.vessel_risk_level,
            e.fishing_hours,
            e.fishing_date,
            e.occurrence_id,
            e.species_common_name,
            e.species_scientific_name,
            e.species_taxa_group,
            e.occurrence_date,
            e.h3_cell,
            e.distance_meters,
            fe.latitude AS lat,
            fe.longitude AS lon
        FROM public.maritime_ecological_encounters e
        LEFT JOIN public.gfw_fishing_effort fe ON e.effort_id = fe.id
    """)).mappings().all()

    print(f"[Programmatic Graph] Retrieved {len(vessels)} vessels, {len(occurrences)} occurrences, "
          f"{len(permits)} permits, {len(encounters)} encounters.")

    # Compute HexCell counts dynamically
    hex_cells = {}
    for enc in encounters:
        cell = enc["h3_cell"]
        if not cell:
            continue
        if cell not in hex_cells:
            hex_cells[cell] = {
                "vessels": set(),
                "megafauna_count": 0,
                "risk_scores": []
            }
        if enc["vessel_id"]:
            hex_cells[cell]["vessels"].add(enc["vessel_id"])
        if enc["occurrence_id"]:
            hex_cells[cell]["megafauna_count"] += 1
        if enc["vessel_risk_score"] is not None:
            hex_cells[cell]["risk_scores"].append(float(enc["vessel_risk_score"]))

    for occ in occurrences:
        cell = occ["h3_index"]
        if not cell:
            continue
        if cell not in hex_cells:
            hex_cells[cell] = {
                "vessels": set(),
                "megafauna_count": 0,
                "risk_scores": []
            }
        hex_cells[cell]["megafauna_count"] += 1

    nodes = []
    node_ids = set()

    def add_node(node):
        if node["id"] not in node_ids:
            nodes.append(node)
            node_ids.add(node["id"])

    # 1. Add VesselIdentity nodes
    for v in vessels:
        mmsi = v["mmsi"]
        v_id = f"vi_{mmsi}" if mmsi else f"vi_vessel_{v['vessel_id']}"
        add_node({
            "id": v_id,
            "type": "VesselIdentity",
            "mmsi": mmsi or "",
            "shipname": v["shipname"] or "Unknown",
            "flag": v["flag"] or "MEX",
            "vessel_type": v["vessel_type"] or "fishing",
            "risk_score": v["risk_score"] or 0.0,
            "risk_level": v["risk_level"] or "low"
        })

    # 2. Add HexCell nodes
    for cell_id, info in hex_cells.items():
        avg_risk = sum(info["risk_scores"]) / len(info["risk_scores"]) if info["risk_scores"] else 0.0
        add_node({
            "id": cell_id,
            "type": "HexCell",
            "h3_index": cell_id,
            "risk_score": round(avg_risk, 2),
            "vessel_count": len(info["vessels"]),
            "megafauna_count": info["megafauna_count"]
        })

    # 3. Add VesselEvent nodes (from encounters)
    for enc in encounters:
        event_id = f"ve_{enc['effort_id']}"
        vessel = next((v for v in vessels if v["vessel_id"] == enc["vessel_id"]), None)
        mmsi_val = vessel["mmsi"] if vessel else enc["vessel_id"].replace("gfw-vessel-", "")
        add_node({
            "id": event_id,
            "type": "VesselEvent",
            "mmsi": mmsi_val,
            "timestamp": str(enc["fishing_date"]) if enc["fishing_date"] else "2026-03-12 13:45:41 UTC",
            "lat": float(enc["lat"]) if enc["lat"] is not None else 24.5,
            "lon": float(enc["lon"]) if enc["lon"] is not None else -110.2
        })

    # 4. Add Species nodes
    for occ in occurrences:
        species_id = f"species_{occ['scientific_name'].lower().replace(' ', '_')}"
        add_node({
            "id": species_id,
            "type": "Species",
            "scientific_name": occ["scientific_name"],
            "species": occ["species"] or occ["scientific_name"],
            "taxa_group": occ["taxa_group"] or ""
        })

    # 5. Add Permit nodes
    for p in permits:
        permit_id = f"permit_{p['rnp']}_{p['species_permit'].lower().replace(' ', '_')}"
        add_node({
            "id": permit_id,
            "type": "Permit",
            "rnp": p["rnp"],
            "species_permit": p["species_permit"],
            "fishing_gear_type": p["fishing_gear_type"] or "",
            "vessel_registration_number": p["vessel_registration_number"] or "",
            "year": p["year"]
        })

    # Add edges/links
    links = []
    link_keys = set()

    def add_link(source, target, relation):
        if source not in node_ids or target not in node_ids:
            return
        key = (source, target, relation)
        if key not in link_keys:
            links.append({
                "source": source,
                "target": target,
                "relation": relation,
                "confidence": "EXTRACTED",
                "confidence_score": 1.0
            })
            link_keys.add(key)

    for enc in encounters:
        event_id = f"ve_{enc['effort_id']}"
        
        # IS_CLASS: VesselEvent -> VesselIdentity
        vessel = next((v for v in vessels if v["vessel_id"] == enc["vessel_id"]), None)
        if vessel:
            v_id = f"vi_{vessel['mmsi']}" if vessel['mmsi'] else f"vi_vessel_{vessel['vessel_id']}"
            add_link(event_id, v_id, "IS_CLASS")
            
        # DETECTED_IN: VesselEvent -> HexCell
        cell = enc["h3_cell"]
        if cell:
            add_link(event_id, cell, "DETECTED_IN")

        # ENCOUNTERED: VesselEvent -> Species
        if enc["species_scientific_name"]:
            species_id = f"species_{enc['species_scientific_name'].lower().replace(' ', '_')}"
            add_link(event_id, species_id, "ENCOUNTERED")

    # BORDERS: HexCell -> HexCell
    try:
        import h3
        cells_list = list(hex_cells.keys())
        for i in range(len(cells_list)):
            for j in range(i + 1, len(cells_list)):
                c1 = cells_list[i]
                c2 = cells_list[j]
                is_neighbor = False
                if hasattr(h3, 'are_neighbor_cells'):
                    is_neighbor = h3.are_neighbor_cells(c1, c2)
                elif hasattr(h3, 'h3_indexes_are_neighbors'):
                    is_neighbor = h3.h3_indexes_are_neighbors(c1, c2)
                if is_neighbor:
                    add_link(c1, c2, "BORDERS")
    except Exception:
        pass

    # HAS_PERMIT: VesselIdentity -> Permit
    for p in permits:
        reg_num = p["vessel_registration_number"]
        if not reg_num:
            continue
        reg_num_clean = reg_num.strip().lower()
        for v in vessels:
            v_mmsi = v["mmsi"] or ""
            v_name = v["shipname"] or ""
            v_id = f"vi_{v['mmsi']}" if v['mmsi'] else f"vi_vessel_{v['vessel_id']}"
            permit_id = f"permit_{p['rnp']}_{p['species_permit'].lower().replace(' ', '_')}"
            if reg_num_clean in v_mmsi or reg_num_clean in v_name.lower():
                add_link(v_id, permit_id, "HAS_PERMIT")

    # Group into simple connected component communities (ignoring HexCells to avoid huge single component)
    adj = {n["id"]: set() for n in nodes if n["type"] != "HexCell"}
    for l in links:
        u = l["source"]
        v = l["target"]
        if u in adj and v in adj:
            adj[u].add(v)
            adj[v].add(u)

    visited = set()
    community_id = 0
    node_community = {}

    for n in nodes:
        nid = n["id"]
        if n["type"] == "HexCell":
            continue
        if nid not in visited:
            queue = [nid]
            visited.add(nid)
            while queue:
                curr = queue.pop(0)
                node_community[curr] = community_id
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            community_id += 1

    hex_cell_community = community_id
    for n in nodes:
        if n["type"] == "HexCell":
            node_community[n["id"]] = hex_cell_community

    for n in nodes:
        n["community"] = node_community.get(n["id"], 0)

    return {
        "directed": False,
        "multigraph": False,
        "graph": {"hyperedges": []},
        "nodes": nodes,
        "links": links
    }

def merge_graphs(prog_graph: dict, cli_graph: dict) -> dict:
    """Merges semantic nodes/links extracted by graphify CLI into the programmatic graph."""
    print("[Merge] Merging graphify CLI output into programmatic graph...")
    prog_node_ids = {n["id"] for n in prog_graph["nodes"]}
    
    # Merge CLI nodes, mapping types if possible
    for node in cli_graph.get("nodes", []):
        if node["id"] not in prog_node_ids:
            t = node.get("type", "")
            if "vessel" in t.lower() or "vi_" in node["id"]:
                node["type"] = "VesselIdentity"
            elif "encounter" in t.lower() or "event" in t.lower() or "ve_" in node["id"]:
                node["type"] = "VesselEvent"
            elif "h3" in t.lower() or "cell" in t.lower():
                node["type"] = "HexCell"
                
            if "mmsi" not in node and "vi_" in node["id"]:
                node["mmsi"] = node["id"].replace("vi_", "")
                
            prog_graph["nodes"].append(node)
            prog_node_ids.add(node["id"])
            
    # Merge CLI links
    prog_links = prog_graph["links"]
    link_keys = {(l["source"], l["target"], l.get("relation", "")) for l in prog_links}
    for link in cli_graph.get("links", []):
        src = link["source"]
        tgt = link["target"]
        rel = link.get("relation", link.get("label", "RELATED_TO"))
        
        if rel == "is":
            rel = "IS_CLASS"
        elif rel == "detected":
            rel = "DETECTED_IN"
            
        key = (src, tgt, rel)
        if key not in link_keys and src in prog_node_ids and tgt in prog_node_ids:
            prog_graph["links"].append({
                "source": src,
                "target": tgt,
                "relation": rel,
                "confidence": link.get("confidence", "EXTRACTED"),
                "confidence_score": link.get("confidence_score", 1.0)
            })
            link_keys.add(key)
            
    return prog_graph

def store_graph(graph_data: dict, db_url: str, dry_run: bool = False):
    """Loads the final graph JSON into public.knowledge_graph and Supabase."""
    if dry_run:
        print("[Store] DRY-RUN: Skipping database storage.")
        return

    # 1. Update local database
    if db_url:
        print(f"[Store] Storing to database: {db_url.split('@')[-1]}")
        try:
            engine = create_engine(db_url)
            query = text("""
                INSERT INTO public.knowledge_graph (graph_name, data, updated_at)
                VALUES (:graph_name, :data, NOW())
                ON CONFLICT (graph_name)
                DO UPDATE SET data = EXCLUDED.data, updated_at = NOW();
            """)
            with engine.connect() as conn:
                conn.execute(query, {
                    "graph_name": "OceanProto Knowledge Graph",
                    "data": json.dumps(graph_data)
                })
                conn.commit()
            print("[Store] Successfully saved graph into local PostgreSQL database.")
        except Exception as e:
            print(f"[Store] Error storing to local Postgres: {e}")

    # 2. Update Supabase if credentials exist
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")
    if supabase_url and supabase_key:
        print(f"[Store] Storing to Supabase REST API: {supabase_url}")
        try:
            import requests
            url = f"{supabase_url}/rest/v1/knowledge_graph?on_conflict=graph_name"
            headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates"
            }
            body = [{
                "graph_name": "OceanProto Knowledge Graph",
                "data": graph_data,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }]
            res = requests.post(url, headers=headers, json=body)
            if res.status_code in (200, 201, 204):
                print("[Store] Successfully uploaded graph to Supabase.")
            else:
                print(f"[Store] Failed to upload to Supabase: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            print(f"[Store] Error uploading to Supabase: {e}")

def main():
    parser = argparse.ArgumentParser(description="LOGR Graphify Exporter Pipeline")
    parser.add_argument("--db-url", default=DATABASE_URL, help="PostgreSQL connection string")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to database")
    parser.add_argument("--full", action="store_true", help="Force complete database graph building")
    parser.add_argument("--limit-corpus", type=int, default=10, help="Maximum items of each type to write to markdown corpus")
    args = parser.parse_args()

    print("=========================================")
    print("      LOGR Graphify Exporter Pipeline    ")
    print("=========================================")
    
    # 1. Connect to database and retrieve data
    engine = create_engine(args.db_url)
    with engine.connect() as conn:
        # Generate complete programmatic graph
        prog_graph = generate_programmatic_graph(conn)
        
        # Now query a subset for the Markdown temp_corpus (to avoid LLM costs/timeouts)
        vessels = conn.execute(text("SELECT * FROM public.gfw_vessels LIMIT :lim"), {"lim": args.limit_corpus}).mappings().all()
        occurrences = conn.execute(text("SELECT * FROM public.obis_occurrences LIMIT :lim"), {"lim": args.limit_corpus}).mappings().all()
        permits = conn.execute(text("SELECT * FROM public.conapesca_permits LIMIT :lim"), {"lim": args.limit_corpus}).mappings().all()
        encounters = conn.execute(text("""
            SELECT e.*, fe.latitude AS lat, fe.longitude AS lon
            FROM public.maritime_ecological_encounters e
            LEFT JOIN public.gfw_fishing_effort fe ON e.effort_id = fe.id
            LIMIT :lim
        """), {"lim": args.limit_corpus}).mappings().all()

    # 2. Write structured markdown to temp_corpus
    temp_dir = Path("dw/temp_corpus")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[Corpus] Generating markdown corpus in {temp_dir} (limit: {args.limit_corpus} per table)...")
    
    # Write Vessels
    for v in vessels:
        v_file = temp_dir / f"vessel_{v['vessel_id']}.md"
        v_file.write_text(f"""# Vessel {v['shipname'] or 'Unknown'}
- Vessel ID: {v['vessel_id']}
- MMSI: {v['mmsi'] or 'Unknown'}
- Flag: {v['flag'] or 'Unknown'}
- Type: {v['vessel_type'] or 'Unknown'}
- Risk Level: {v['risk_level'] or 'Unknown'}
- Risk Score: {v['risk_score'] or 0.0}
- Last Seen: {v['last_seen'] or 'Unknown'}
""", encoding="utf-8")

    # Write Occurrences
    for o in occurrences:
        o_file = temp_dir / f"obis_{o['id']}.md"
        o_file.write_text(f"""# Species Occurrence {o['id']}
- Species: {o['species'] or 'Unknown'}
- Scientific Name: {o['scientific_name'] or 'Unknown'}
- Taxa Group: {o['taxa_group'] or 'Unknown'}
- Date: {o['event_date'] or 'Unknown'}
- Location: ({o['latitude']}, {o['longitude']})
- H3 Cell: {o['h3_index'] or 'Unknown'}
""", encoding="utf-8")

    # Write Permits
    for p in permits:
        p_file = temp_dir / f"permit_{p['id']}.md"
        p_file.write_text(f"""# CONAPESCA Permit {p['id']}
- RNP: {p['rnp'] or 'Unknown'}
- Species Permit: {p['species_permit'] or 'Unknown'}
- Year: {p['year'] or 'Unknown'}
- Vessel Registration Number: {p['vessel_registration_number'] or 'Unknown'}
- Fishing Gear Type: {p['fishing_gear_type'] or 'Unknown'}
""", encoding="utf-8")

    # Write Encounters
    for e in encounters:
        e_file = temp_dir / f"encounter_{e['effort_id']}_{e['occurrence_id']}.md"
        e_file.write_text(f"""# Ecological Encounter
- Vessel Name: {e['vessel_name'] or 'Unknown'}
- Vessel ID: {e['vessel_id']}
- Species Scientific Name: {e['species_scientific_name'] or 'Unknown'}
- Species Common Name: {e['species_common_name'] or 'Unknown'}
- Fishing Date: {e['fishing_date'] or 'Unknown'}
- H3 Cell: {e['h3_cell'] or 'Unknown'}
- Distance: {e['distance_meters'] or 0.0} meters
""", encoding="utf-8")

    # 3. Run graphify CLI with programmatic fallback
    final_graph = None
    try:
        cli_graph = run_graphify_cli(temp_dir)
        final_graph = merge_graphs(prog_graph, cli_graph)
    except Exception as e:
        print(f"[Pipeline] graphify CLI run failed: {e}")
        print("[Pipeline] Falling back to pure programmatic graph generation.")
        final_graph = prog_graph

    # 4. Clean up temp corpus directory
    print("[Corpus] Cleaning up temp_corpus directory...")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    # 5. Store graph
    if final_graph:
        print(f"[Pipeline] Final Graph has {len(final_graph['nodes'])} nodes and {len(final_graph['links'])} links.")
        store_graph(final_graph, args.db_url, args.dry_run)
        print("[Pipeline] Graphify export pipeline completed successfully.")
    else:
        print("[Pipeline] Error: No graph generated.")
        sys.exit(1)

if __name__ == "__main__":
    main()

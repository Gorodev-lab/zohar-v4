#!/usr/bin/env python3
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
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
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

PRESETS = [
    {
        "id": "rep_california",
        "label": "Reporte Golfo de California",
        "min_lat": 22.0, "max_lat": 32.0,
        "min_lon": -115.0, "max_lon": -105.0,
        "dataset": "public-global-vessel-identity:latest"
    },
    {
        "id": "rep_pacific",
        "label": "Reporte Pacífico Mexicano (Norte)",
        "min_lat": 20.0, "max_lat": 32.0,
        "min_lon": -120.0, "max_lon": -110.0,
        "dataset": "public-global-vessel-identity:latest"
    },
    {
        "id": "rep_campeche",
        "label": "Reporte Sonda de Campeche",
        "min_lat": 18.0, "max_lat": 21.0,
        "min_lon": -93.0, "max_lon": -90.0,
        "dataset": "public-global-offshore-infrastructure:latest"
    }
]

def generate_csv_data(conn, min_lat, max_lat, min_lon, max_lon):
    # Query vessels in bounding box
    vessel_q = text("""
        SELECT vessel_id, mmsi, shipname, flag, vessel_type, risk_score, risk_level, last_seen
        FROM public.gfw_vessels
        WHERE ST_Contains(
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326),
            geom
        )
    """)
    vessels = conn.execute(vessel_q, {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).mappings().all()

    # Query occurrences in bounding box
    occ_q = text("""
        SELECT id, species, scientific_name, event_date, latitude, longitude, taxa_group
        FROM public.obis_occurrences
        WHERE ST_Contains(
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326),
            geom
        )
    """)
    occurrences = conn.execute(occ_q, {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).mappings().all()

    # Query encounters in bounding box (joins fishing effort within bounds)
    enc_q = text("""
        SELECT 
            e.vessel_name, e.vessel_flag, e.vessel_type, e.vessel_risk_score, e.fishing_hours, 
            e.fishing_date, e.species_scientific_name, e.distance_meters, fe.latitude, fe.longitude
        FROM public.maritime_ecological_encounters e
        JOIN public.gfw_fishing_effort fe ON e.effort_id = fe.id
        WHERE ST_Contains(
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326),
            fe.geom
        )
    """)
    encounters = conn.execute(enc_q, {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).mappings().all()

    # Build CSV
    lines = []
    lines.append("=== DETAILED LOGR MARITIME HISTORICAL REPORT ===")
    lines.append(f"Generated At: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Bounding Box: Lat [{min_lat}, {max_lat}] | Lon [{min_lon}, {max_lon}]")
    lines.append("")

    # Section 1: Vessels
    lines.append("--- SECTION 1: GLOBAL FISHING WATCH VESSELS ---")
    lines.append("vessel_id,mmsi,shipname,flag,vessel_type,risk_score,risk_level,last_seen")
    for v in vessels:
        lines.append(f"{v['vessel_id']},{v['mmsi'] or ''},{v['shipname'] or ''},{v['flag'] or ''},{v['vessel_type'] or ''},{v['risk_score'] or 0.0},{v['risk_level'] or ''},{v['last_seen'] or ''}")
    lines.append("")

    # Section 2: OBIS Occurrences
    lines.append("--- SECTION 2: OBIS ECOLOGICAL OCCURRENCES ---")
    lines.append("id,species,scientific_name,event_date,latitude,longitude,taxa_group")
    for o in occurrences:
        lines.append(f"{o['id']},{o['species'] or ''},{o['scientific_name'] or ''},{o['event_date'] or ''},{o['latitude']},{o['longitude']},{o['taxa_group'] or ''}")
    lines.append("")

    # Section 3: Encounters
    lines.append("--- SECTION 3: SPATIAL-TEMPORAL ECOLOGICAL ENCOUNTERS ---")
    lines.append("vessel_name,vessel_flag,vessel_type,vessel_risk_score,fishing_hours,fishing_date,species_scientific_name,distance_meters,latitude,longitude")
    for e in encounters:
        lines.append(f"{e['vessel_name'] or ''},{e['vessel_flag'] or ''},{e['vessel_type'] or ''},{e['vessel_risk_score'] or 0.0},{e['fishing_hours'] or 0.0},{e['fishing_date'] or ''},{e['species_scientific_name'] or ''},{e['distance_meters'] or 0.0},{e['latitude']},{e['longitude']}")

    return "\n".join(lines)


def generate_readme(preset_name, min_lat, max_lat, min_lon, max_lon):
    return f"""# LOGR GFW Report: {preset_name}
===========================================
This report contains high-resolution historical fishing trajectories, biological occurrences, 
and ecological overlaps extracted from the LOGR Maritime Data Warehouse.

Region boundary details:
- Latitudes:  [{min_lat}, {max_lat}]
- Longitudes: [{min_lon}, {max_lon}]

Files:
- DATA: Detailed CSV logs of GFW vessels and spatial encounters.
- GEOM: Bounds polygon representation.
"""

def upload_to_supabase(key, data, ttl_days=365):
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        print("[Supabase] Missing credentials, skipping upload.")
        return
        
    url = f"{SUPABASE_URL}/rest/v1/gfw_cache?on_conflict=cache_key"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    body = {
        "cache_key": key,
        "data": data,
        "expires_at": expires_at
    }
    
    res = requests.post(url, headers=headers, json=body)
    if res.status_code in (200, 201, 204):
        print(f"[Supabase] Successfully cached key: {key}")
    else:
        print(f"[Supabase] Failed to cache key {key}: HTTP {res.status_code} - {res.text}")

def main():
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        print("Error: Supabase credentials missing. Populate .env.local first.")
        sys.exit(1)
        
    engine = create_engine(DATABASE_URL)
    
    # Generate and upload reports
    reports_metadata = []
    
    with engine.connect() as conn:
        for pr in PRESETS:
            print(f"\n[Generate] Processing preset: {pr['label']}...")
            
            # Generate DATA (CSV)
            csv_content = generate_csv_data(conn, pr["min_lat"], pr["max_lat"], pr["min_lon"], pr["max_lon"])
            csv_key = f"gfw:bulk-reports:data:{pr['id']}:DATA"
            upload_to_supabase(csv_key, csv_content)
            
            # Generate README (Markdown)
            readme_content = generate_readme(pr["label"], pr["min_lat"], pr["max_lat"], pr["min_lon"], pr["max_lon"])
            readme_key = f"gfw:bulk-reports:data:{pr['id']}:README"
            upload_to_supabase(readme_key, readme_content)
            
            # Metadata
            reports_metadata.append({
                "id": pr["id"],
                "label": pr["label"],
                "dataset": pr["dataset"],
                "status": "done",
                "startDate": "2025-06-01",
                "endDate": "2026-03-01",
                "createdAt": datetime.now(timezone.utc).strftime("%d/%m/%Y, %H:%M:%S UTC"),
                "bounds": {
                    "minLat": pr["min_lat"],
                    "maxLat": pr["max_lat"],
                    "minLon": pr["min_lon"],
                    "maxLon": pr["max_lon"]
                }
            })
            
    # Save reports list metadata
    list_key = "gfw:bulk-reports:list"
    upload_to_supabase(list_key, reports_metadata)
    print("\n=== POPULATION COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()

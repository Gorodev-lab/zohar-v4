#!/usr/bin/env python3
import os
import sys
import numpy as np
import json
import random
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Add project root to path to import ml.jepa
sys.path.append(str(Path(__file__).parent.parent.parent))

try:
    import torch
    from ml.jepa.encoder import TrajectoryEncoder
    from ml.jepa.predictor import LatentPredictor
    from ml.jepa.train import get_device
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    print("[JEPA Inference] PyTorch not installed. Running in graceful mock CPU inference fallback mode.")

# Load environment variables
for p in [Path('.'), Path('..'), Path(__file__).parent.parent.parent]:
    for env_file in ['.env.local', '.env']:
        env_path = p / env_file
        if env_path.exists():
            load_dotenv(env_path)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

# Expert OSINT intelligence profiles for context enrichment
OSINT_PROFILES = {
    "HALO": {
        "ubo": "Barry Sternlicht (American billionaire, founder of Starwood Capital)",
        "builder": "Feadship (Netherlands, 2015)",
        "imo": "1012775",
        "class": "Superyacht (57.47m, 1,001 GT, Cayman Islands Flag)"
    },
    "AV": {
        "ubo": "Dennis Washington (American billionaire industrialist)",
        "builder": "Blohm+Voss (Germany, 2010)",
        "imo": "1010167",
        "class": "Superyacht (95.15m, 4,440 GT, ex-Palladium, Cayman Islands Flag)"
    },
    "MAHALO": {
        "ubo": "Sin confirmación (Operación recreativa local)",
        "builder": "Custom Line",
        "imo": "Sin confirmación",
        "class": "Yate Recreativo (12.1m, US Flag)"
    }
}

def load_vessel_registry():
    try:
        registry_path = Path(__file__).parent.parent.parent / "data" / "vessel_registry.json"
        if registry_path.exists():
            with open(registry_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[JEPA Inference] Error loading vessel registry: {e}")
    return {}

def get_conapesca_permits_for_vessel(permit_id):
    if not permit_id:
        return []
    permits = []
    try:
        csv_path = Path(__file__).parent.parent.parent / "data" / "CONAPESCA-Permits_extract.csv"
        if csv_path.exists():
            import csv
            with open(csv_path, 'r', encoding='latin1') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    clean_rnp = row['RNP'].split('.')[0].strip()
                    clean_permit_id = str(permit_id).split('.')[0].strip()
                    if clean_rnp == clean_permit_id:
                        permits.append({
                            "year": row.get("year"),
                            "rnp": row.get("RNP"),
                            "vessel_reg": row.get("vessel_registration_number"),
                            "species": row.get("species_permit"),
                            "gear_num": row.get("fishing_gear_number"),
                            "gear_type": row.get("fishing_gear_type_en")
                        })
    except Exception as e:
        print(f"[JEPA Context Enrichment] Error reading permits CSV: {e}")
    return permits

def get_dynamic_kinematic_modifier(vessel_name, flag_state, vessel_class, length, tonnage, permits):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[JEPA Context Enrichment] GEMINI_API_KEY missing. Using baseline fallback modifier.")
        return 1.0

    # Format the vessel permits and specifications in TONL v1.0
    permits_tonl_list = []
    for p in permits:
        permits_tonl_list.append(f"  - year: {p['year']}, gear: {p['gear_type']}, species: {p['species']}")
    permits_tonl = "\n".join(permits_tonl_list) if permits_tonl_list else "  - None"

    tonl_profile = (
        f"#version 1.0\n"
        f"vessel_profile:\n"
        f"  name: {vessel_name}\n"
        f"  flag: {flag_state or 'Unknown'}\n"
        f"  class: {vessel_class or 'Unknown'}\n"
        f"  length_m: {length or 'Unknown'}\n"
        f"  tonnage_gt: {tonnage or 'Unknown'}\n"
        f"  conapesca_permits:\n"
        f"{permits_tonl}"
    )

    prompt = (
        "[ROLE]\n"
        "Eres un modelo de preprocesamiento de Machine Learning físico-naval para la plataforma LOGR.\n\n"
        "[DATA IN TONL (Token-Optimized Notation Language) FORMAT]\n"
        f"{tonl_profile}\n\n"
        "[TONL FRAMEWORK FOR INFERENCE]\n"
        "- **TASK (T):**\n"
        "  Determina el factor de predictibilidad de inercia y cinemática (kinematic_modifier) de la embarcación.\n"
        "- **OPERATORS (O):**\n"
        "  * Los barcos de gran tonelaje (>1,000 GT) o gran longitud (>50m) tienen una inercia extremadamente alta y se mueven de forma muy predecible (kinematic_modifier entre 0.75 y 0.85).\n"
        "  * Los yates recreativos y pangas tienen baja inercia y gran maniobrabilidad (kinematic_modifier entre 0.90 y 1.10).\n"
        "  * Las naves con permisos de redes de arrastre de CONAPESCA reducen su velocidad esperada significativamente (kinematic_modifier de arrastre alrededor de 0.80).\n"
        "- **LIMITS (L):**\n"
        "  Devuelve ÚNICAMENTE un objeto JSON con la clave 'kinematic_modifier' (float) y la clave 'reason' (string), sin formato markdown ni explicaciones adicionales.\n"
    )

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result = json.loads(response.text)
        modifier = float(result.get("kinematic_modifier", 1.0))
        reason = result.get("reason", "Dynamic evaluation completed.")
        print(f"  [JEPA Dynamic Preprocessor] Dynamic modifier calculated: {modifier:.2f} ({reason})")
        return max(0.5, min(1.5, modifier))
    except Exception as e:
        print(f"  [JEPA Dynamic Preprocessor] Error fetching dynamic modifier: {e}. Falling back to 1.0.")
        return 1.0


def run_mock_jepa_inference_on_gaps():
    print("[JEPA Mock Inference] Connecting to database...")
    engine = create_engine(DATABASE_URL)
    vessel_registry = load_vessel_registry()

    query_gaps = text("""
        SELECT id, vessel_id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, description
        FROM traffic_anomalies
        WHERE anomaly_type = 'AIS Gap' AND jepa_behavior_embedding IS NULL;
    """)
    
    with engine.connect() as conn:
        gaps = conn.execute(query_gaps).fetchall()
        print(f"[JEPA Mock Inference] Found {len(gaps)} AIS Gaps without JEPA embeddings.")
        
        if not gaps:
            print("[JEPA Mock Inference] No gaps to process. Exiting.")
            return

        has_pgvector = False
        try:
            res = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
            if res:
                has_pgvector = True
        except Exception:
            pass
        print(f"[JEPA Mock Inference] DB has pgvector extension: {has_pgvector}")

        for gap in gaps:
            gap_id, vessel_id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, base_desc = gap
            base_desc = base_desc or "AIS transponder turned off."
            
            # Fetch vessel details
            query_vessel = text("""
                SELECT name, flag_state, permit_id
                FROM vessels
                WHERE id = :vessel_id;
            """)
            vessel_info = conn.execute(query_vessel, {"vessel_id": vessel_id}).fetchone()
            
            vessel_name = "Unknown Vessel"
            flag_state = None
            permit_id = None
            if vessel_info:
                vessel_name = vessel_info[0]
                flag_state = vessel_info[1]
                permit_id = vessel_info[2] if len(vessel_info) > 2 else None
            
            # Match with registry and OSINT profiles
            reg_match = None
            osint_profile = None
            
            for name_key, profile in OSINT_PROFILES.items():
                if name_key.upper() in vessel_name.upper():
                    osint_profile = profile
                    vessel_name = name_key
                    break
                    
            for mmsi_key, reg_val in vessel_registry.items():
                shipname_reg = reg_val.get('shipname', '')
                if shipname_reg and shipname_reg.upper() == vessel_name.upper():
                    reg_match = reg_val
                    break
            
            enriched_desc = base_desc
            kinematic_modifier = 1.0
            permits = get_conapesca_permits_for_vessel(permit_id)
            
            if osint_profile or reg_match:
                print(f"[JEPA Context Enrichment] Identified yacht/superyacht: {vessel_name}")
                length = reg_match.get('length_m') if reg_match else None
                tonnage = reg_match.get('tonnage_gt') if reg_match else None
                imo = osint_profile.get('imo') if osint_profile else (reg_match.get('imo', 'Sin confirmación') if reg_match else 'Sin confirmación')
                ubo = osint_profile.get('ubo') if osint_profile else 'Sin confirmación'
                vclass = osint_profile.get('class') if osint_profile else f"Pleasure Craft ({length}m)"
                builder = osint_profile.get('builder') if osint_profile else 'Sin confirmación'
                
                kinematic_modifier = get_dynamic_kinematic_modifier(
                    vessel_name, flag_state, vclass, length, tonnage, permits
                )
                
                enriched_desc = (
                    f"{base_desc} | [OSINT Context Enriched] "
                    f"Asset: {vessel_name} ({vclass}) | IMO: {imo} | "
                    f"UBO Owner: {ubo} | Builder: {builder} | "
                    f"Flag: {flag_state or reg_match.get('flag') or 'CYM'}."
                )
                print(f"  -> Context enriched: {enriched_desc[:90]}...")
            elif permits:
                kinematic_modifier = get_dynamic_kinematic_modifier(
                    vessel_name, flag_state, "Commercial Fishing Vessel", None, None, permits
                )

            # Generate random 128d vector and normalize it
            raw_vec = [random.uniform(-1.0, 1.0) for _ in range(128)]
            norm = sum(x*x for x in raw_vec) ** 0.5
            embedding_vector = [x / norm for x in raw_vec]

            if has_pgvector:
                embedding_str = "[" + ",".join(map(str, embedding_vector)) + "]"
                update_query = text("""
                    UPDATE traffic_anomalies
                    SET jepa_behavior_embedding = CAST(:embedding_str AS vector),
                        description = :enriched_desc
                    WHERE id = :gap_id;
                """)
            else:
                embedding_str = "{" + ",".join(map(str, embedding_vector)) + "}"
                update_query = text("""
                    UPDATE traffic_anomalies
                    SET jepa_behavior_embedding = CAST(:embedding_str AS float8[]),
                        description = :enriched_desc
                    WHERE id = :gap_id;
                """)
            
            conn.execute(update_query, {
                "embedding_str": embedding_str, 
                "enriched_desc": enriched_desc,
                "gap_id": gap_id
            })
            conn.commit()
            print(f"[JEPA Mock Inference] Computed mock 128d embedding and updated description for anomaly ID {gap_id}.")

    print("[JEPA Mock Inference] All Gaps processed successfully with mock inference fallback.")


def run_jepa_inference_on_gaps():
    if not HAS_TORCH:
        run_mock_jepa_inference_on_gaps()
        return

    device = get_device()
    print(f"[JEPA Inference] Connecting to database...")
    engine = create_engine(DATABASE_URL)
    
    # 1. Initialize models
    trajectory_encoder = TrajectoryEncoder()
    latent_predictor = LatentPredictor(action_dim=2)
    
    # Load weights if they exist (otherwise use initialized weights for demonstration)
    weights_encoder_path = Path(__file__).parent / "weights" / "trajectory_encoder.pt"
    weights_predictor_path = Path(__file__).parent / "weights" / "latent_predictor.pt"
    
    if weights_encoder_path.exists() and weights_predictor_path.exists():
        print(f"[JEPA Inference] Loading pre-trained weights from {weights_encoder_path.parent}...")
        trajectory_encoder.load_state_dict(torch.load(weights_encoder_path, map_location='cpu'))
        latent_predictor.load_state_dict(torch.load(weights_predictor_path, map_location='cpu'))
    else:
        print("[JEPA Inference] No pre-trained weights found. Running mock training to generate base weights...")
        try:
            from ml.jepa.train import run_training_loop
            run_training_loop(epochs=2)
            if weights_encoder_path.exists():
                trajectory_encoder.load_state_dict(torch.load(weights_encoder_path, map_location='cpu'))
                latent_predictor.load_state_dict(torch.load(weights_predictor_path, map_location='cpu'))
        except Exception as e:
            print(f"[JEPA Inference] Mock training failed: {e}. Using initialized random weights.")

    trajectory_encoder = trajectory_encoder.to(device)
    latent_predictor = latent_predictor.to(device)
    trajectory_encoder.eval()
    latent_predictor.eval()

    vessel_registry = load_vessel_registry()

    # 2. Fetch gaps that need embeddings
    query_gaps = text("""
        SELECT id, vessel_id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, description
        FROM traffic_anomalies
        WHERE anomaly_type = 'AIS Gap' AND jepa_behavior_embedding IS NULL;
    """)
    
    with engine.connect() as conn:
        gaps = conn.execute(query_gaps).fetchall()
        print(f"[JEPA Inference] Found {len(gaps)} AIS Gaps without JEPA embeddings.")
        
        if not gaps:
            print("[JEPA Inference] No gaps to process. Exiting.")
            return

        # Check if pgvector extension is present in DB
        has_pgvector = False
        try:
            res = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
            if res:
                has_pgvector = True
        except Exception:
            pass
        print(f"[JEPA Inference] DB has pgvector extension: {has_pgvector}")
 
        for gap in gaps:
            gap_id, vessel_id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, base_desc = gap
            base_desc = base_desc or "AIS transponder turned off."
            
            # Fetch vessel details
            query_vessel = text("""
                SELECT name, flag_state, permit_id
                FROM vessels
                WHERE id = :vessel_id;
            """)
            vessel_info = conn.execute(query_vessel, {"vessel_id": vessel_id}).fetchone()
            
            vessel_name = "Unknown Vessel"
            flag_state = None
            permit_id = None
            if vessel_info:
                vessel_name = vessel_info[0]
                flag_state = vessel_info[1]
                permit_id = vessel_info[2] if len(vessel_info) > 2 else None
            
            # Match with registry and OSINT profiles
            reg_match = None
            osint_profile = None
            
            # Check OSINT profile by name
            for name_key, profile in OSINT_PROFILES.items():
                if name_key.upper() in vessel_name.upper():
                    osint_profile = profile
                    vessel_name = name_key # Normalize name
                    break
                    
            # Check registry mapping
            for mmsi_key, reg_val in vessel_registry.items():
                shipname_reg = reg_val.get('shipname', '')
                if shipname_reg and shipname_reg.upper() == vessel_name.upper():
                    reg_match = reg_val
                    break
            
            # Context Enrichment Log & Anomaly Description Modification
            enriched_desc = base_desc
            kinematic_modifier = 1.0
            
            # Fetch permits context from CONAPESCA local CSV using permit_id
            permits = get_conapesca_permits_for_vessel(permit_id)
            
            if osint_profile or reg_match:
                print(f"[JEPA Context Enrichment] Identified yacht/superyacht: {vessel_name}")
                
                # Fetch specifications
                length = reg_match.get('length_m') if reg_match else None
                tonnage = reg_match.get('tonnage_gt') if reg_match else None
                vtype = reg_match.get('vessel_type') if reg_match else "Yacht"
                
                imo = osint_profile.get('imo') if osint_profile else (reg_match.get('imo', 'Sin confirmación') if reg_match else 'Sin confirmación')
                ubo = osint_profile.get('ubo') if osint_profile else 'Sin confirmación'
                vclass = osint_profile.get('class') if osint_profile else f"Pleasure Craft ({length}m)"
                builder = osint_profile.get('builder') if osint_profile else 'Sin confirmación'
                
                # Dynamic preprocessor for kinematic modifier using TONL structured prompts on Gemini
                kinematic_modifier = get_dynamic_kinematic_modifier(
                    vessel_name, flag_state, vclass, length, tonnage, permits
                )
                
                # Formulate enriched description containing complete OSINT intelligence context
                enriched_desc = (
                    f"{base_desc} | [OSINT Context Enriched] "
                    f"Asset: {vessel_name} ({vclass}) | IMO: {imo} | "
                    f"UBO Owner: {ubo} | Builder: {builder} | "
                    f"Flag: {flag_state or reg_match.get('flag') or 'CYM'}."
                )
                print(f"  -> Context enriched: {enriched_desc[:90]}...")
            elif permits:
                # Dynamic preprocessor fallback for commercial vessels with permits
                kinematic_modifier = get_dynamic_kinematic_modifier(
                    vessel_name, flag_state, "Commercial Fishing Vessel", None, None, permits
                )
            
            # Fetch vessel telemetry context leading up to the gap (last 12 points)
            query_telemetry = text("""
                SELECT latitude, longitude, speed, course
                FROM telemetry_records
                WHERE vessel_id = :vessel_id AND timestamp < :start_time
                ORDER BY timestamp DESC
                LIMIT 12;
            """)
            
            telemetry = conn.execute(query_telemetry, {"vessel_id": vessel_id, "start_time": start_time}).fetchall()
            
            # Convert context to tensor
            if len(telemetry) >= 3:
                # We have enough history to construct a sequence
                # Reverse to keep chronological order
                telemetry = list(reversed(telemetry))
                # Pad if less than 12
                while len(telemetry) < 12:
                    telemetry.append(telemetry[-1]) # Pad with last observation
                
                # Apply kinematic modifier to speed features
                context_data = []
                for t in telemetry:
                    lat, lon, speed, course = float(t[0]), float(t[1]), float(t[2]), float(t[3])
                    # Modulate speed by the kinematic predictability of the vessel class
                    speed = speed * kinematic_modifier
                    context_data.append([lat, lon, speed, course])
                
                context_tensor = torch.tensor([context_data], dtype=torch.float32) # (1, 12, 4)
            else:
                # Fallback: create mock baseline context if no telemetry exists
                context_tensor = torch.randn(1, 12, 4)
 
            # Calculate gap parameters: [gap_duration_hours, distance_meters]
            duration_hours = 2.0
            if end_time and start_time:
                duration_hours = max(0.1, (end_time - start_time).total_seconds() / 3600.0)
            
            distance_meters = 500.0
            if start_lat and start_lon and end_lat and end_lon:
                distance_meters = np.sqrt((end_lat - start_lat)**2 + (end_lon - start_lon)**2) * 111000.0
                
            # Modulate gap distance projection by kinematic modifier
            distance_km = (distance_meters / 1000.0) * kinematic_modifier
            gap_params_tensor = torch.tensor([[duration_hours, distance_km]], dtype=torch.float32)

            # 3. Perform latent prediction
            with torch.no_grad():
                context_emb = trajectory_encoder(context_tensor.to(device))
                predicted_emb = latent_predictor(context_emb, gap_params_tensor.to(device))
                # Bring back to CPU and convert to list
                embedding_vector = predicted_emb.cpu().squeeze(0).numpy().tolist()

            # 4. Save embedding and enriched description to database
            if has_pgvector:
                embedding_str = "[" + ",".join(map(str, embedding_vector)) + "]"
                update_query = text("""
                    UPDATE traffic_anomalies
                    SET jepa_behavior_embedding = CAST(:embedding_str AS vector),
                        description = :enriched_desc
                    WHERE id = :gap_id;
                """)
            else:
                embedding_str = "{" + ",".join(map(str, embedding_vector)) + "}"
                update_query = text("""
                    UPDATE traffic_anomalies
                    SET jepa_behavior_embedding = CAST(:embedding_str AS float8[]),
                        description = :enriched_desc
                    WHERE id = :gap_id;
                """)
            
            conn.execute(update_query, {
                "embedding_str": embedding_str, 
                "enriched_desc": enriched_desc,
                "gap_id": gap_id
            })
            conn.commit()
            print(f"[JEPA Inference] Computed 128d embedding and updated description for anomaly ID {gap_id}.")

    print("[JEPA Inference] All Gaps processed successfully.")

if __name__ == "__main__":
    run_jepa_inference_on_gaps()

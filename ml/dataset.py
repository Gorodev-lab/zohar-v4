import os
import hashlib
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load environment variables (useful if dataset.py is imported in train.py and run standalone)
for p in [Path('.'), Path('..'), Path(__file__).parent.parent]:
    for env_file in ['.env.local', '.env']:
        env_path = p / env_file
        if env_path.exists():
            load_dotenv(env_path)

def get_h3_index(lat, lng, res=7):
    """Computes H3 index defensively supporting both v3 and v4 of h3 library."""
    try:
        import h3
        lat, lng = float(lat), float(lng)
        if hasattr(h3, 'latlng_to_cell'):
            return h3.latlng_to_cell(lat, lng, res)
        elif hasattr(h3, 'geo_to_h3'):
            return h3.geo_to_h3(lat, lng, res)
    except Exception:
        pass
    return None

class MaritimeMultimodalDataset(Dataset):
    """
    Multimodal Dataset for LOGR containing GPS trajectories, text permissions metadata, 
    and biological occurrences (OBIS density).
    
    Prevents data leakage by ensuring chronological ordering and grouping per vessel.
    """
    def __init__(self, trajectories, texts, bio_densities, seq_len=100):
        """
        trajectories: List of numpy arrays, each of shape [vessel_points, 5] 
                      (lat, lon, speed, course, delta_time)
        texts: List of text strings corresponding to each vessel's permit text.
        bio_densities: List of arrays of shape [vessel_points] containing species density values.
        seq_len: Sliding window size for context/target creation.
        """
        self.seq_len = seq_len
        self.samples = []
        
        # Build sliding windows per vessel to prevent mixing trajectories between different ships
        for v_idx, (traj, text_data, bio) in enumerate(zip(trajectories, texts, bio_densities)):
            n_points = len(traj)
            if n_points < seq_len:
                continue
            
            # Slide window
            for i in range(0, n_points - seq_len + 1, seq_len // 2):
                traj_win = traj[i : i + seq_len]
                bio_win = bio[i : i + seq_len]
                
                self.samples.append({
                    "vessel_index": v_idx,
                    "trajectory": torch.tensor(traj_win, dtype=torch.float32),
                    "text": text_data,
                    "bio_density": torch.tensor(bio_win, dtype=torch.float32).unsqueeze(-1)
                })

        # Pre-compute and cache Gemini text embeddings for unique texts to prevent per-sample API overhead
        self.text_embeddings = {}
        unique_texts = list(set(texts)) if texts else []
        for t in unique_texts:
            self.text_embeddings[t] = self._get_gemini_text_embedding(t, embed_dim=128)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        text_emb = self.text_embeddings[sample["text"]]
        
        return {
            "trajectory": sample["trajectory"],      # [seq_len, 5]
            "text_embedding": text_emb,              # [embed_dim]
            "bio_density": sample["bio_density"]     # [seq_len, 1]
        }

    def _get_gemini_text_embedding(self, text_val, embed_dim=128):
        """Generates real text embedding using Gemini gemini-embedding-2 if API key is present."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return self._get_text_embedding_fallback(text_val, embed_dim)
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            response = client.models.embed_content(
                model="gemini-embedding-2",
                contents=text_val,
                config=types.EmbedContentConfig(output_dimensionality=embed_dim)
            )
            embedding = response.embeddings[0].values
            return torch.tensor(embedding, dtype=torch.float32)
        except Exception as e:
            print(f"[Dataset] Error generating Gemini embedding: {e}. Falling back to pseudo-embedding.")
            return self._get_text_embedding_fallback(text_val, embed_dim)

    def _get_text_embedding_fallback(self, text_val, embed_dim=128):
        """Generates a reproducible pseudo-embedding from text for testing/fallback."""
        # Clean text
        text_val = str(text_val).lower().strip()
        # Seed generator with hash
        h = hashlib.md5(text_val.encode('utf-8')).digest()
        seed = int.from_bytes(h, byteorder='big') % (2**32 - 1)
        rng = np.random.default_rng(seed)
        
        # Generate random normalized vector
        vec = rng.standard_normal(embed_dim)
        vec = vec / (np.linalg.norm(vec) + 1e-9)
        return torch.tensor(vec, dtype=torch.float32)


def chronological_split(db_vessels_data, split_ratio=0.8):
    """
    Splits the trajectory data chronologically per vessel to prevent temporal data leakage.
    
    db_vessels_data: List of dicts, each representing a vessel:
                     {
                        "vessel_id": str,
                        "points": List of dicts (lat, lon, timestamp, speed, course, obis_density),
                        "permit_text": str
                     }
    """
    train_trajectories = []
    train_texts = []
    train_bios = []
    
    val_trajectories = []
    val_texts = []
    val_bios = []

    for v_data in db_vessels_data:
        # 1. Sort points chronologically
        points = sorted(v_data["points"], key=lambda x: x["timestamp"])
        if len(points) < 10:
            continue
        
        # Convert points to array [N, 5] (lat, lon, speed, course, delta_time)
        coords = []
        densities = []
        prev_time = None
        
        for pt in points:
            t = pt["timestamp"]
            if isinstance(t, str):
                t = datetime.fromisoformat(t.replace('Z', '+00:00'))
            
            dt = 0.0
            if prev_time is not None:
                dt = (t - prev_time).total_seconds() / 3600.0  # Hours
                
            prev_time = t
            coords.append([pt["lat"], pt["lon"], pt["speed"], pt["course"], dt])
            densities.append(pt.get("obis_density", 0.0))
            
        coords = np.array(coords, dtype=np.float32)
        densities = np.array(densities, dtype=np.float32)
        
        # Normalize time deltas and inputs locally for stability
        coords[:, 4] = np.clip(coords[:, 4], 0.0, 24.0) # Cap dt at 24h
        
        # Split chronologically
        split_idx = int(len(coords) * split_ratio)
        
        train_trajectories.append(coords[:split_idx])
        train_texts.append(v_data["permit_text"])
        train_bios.append(densities[:split_idx])
        
        val_trajectories.append(coords[split_idx:])
        val_texts.append(v_data["permit_text"])
        val_bios.append(densities[split_idx:])
        
    return (
        (train_trajectories, train_texts, train_bios),
        (val_trajectories, val_texts, val_bios)
    )


def seed_database_telemetry(conn):
    """Seeds the database with vessels and telemetry records if sparse or empty."""
    import uuid
    import random
    from datetime import datetime, timedelta
    
    # 5 vessels with real RNP permit numbers present in conapesca_permits
    vessels_to_seed = [
        {"name": "PESCADOR INDEPENDIENTE", "permit_id": "302000567", "flag_state": "MEX"},
        {"name": "TRAMPA COSTERA", "permit_id": "302001128", "flag_state": "MEX"},
        {"name": "ATUNERO CORONADO", "permit_id": "302001169", "flag_state": "MEX"},
        {"name": "MARLIN BLANCO", "permit_id": "302001250", "flag_state": "MEX"},
        {"name": "TIBURONERO DE LA PAZ", "permit_id": "302001268", "flag_state": "MEX"}
    ]
    
    print("[Dataset Seed] Database has insufficient data. Seeding vessels...")
    vessel_ids = []
    for v in vessels_to_seed:
        # Check if already exists
        check_vessel = conn.execute(text("SELECT id FROM vessels WHERE name = :name"), {"name": v["name"]}).fetchone()
        if check_vessel:
            v_id = check_vessel[0]
        else:
            v_id = uuid.uuid4()
            conn.execute(text("""
                INSERT INTO vessels (id, name, permit_id, flag_state, created_at)
                VALUES (:id, :name, :permit_id, :flag_state, now())
            """), {"id": v_id, "name": v["name"], "permit_id": v["permit_id"], "flag_state": v["flag_state"]})
        vessel_ids.append((v_id, v["name"]))
        
    print("[Dataset Seed] Seeding telemetry_records (350 records per vessel)...")
    start_time = datetime(2026, 1, 1, 0, 0, 0)
    for v_id, v_name in vessel_ids:
        # Check if telemetry already exists
        count = conn.execute(text("SELECT count(*) FROM telemetry_records WHERE vessel_id = :v_id"), {"v_id": v_id}).fetchone()[0]
        if count >= 350:
            continue
            
        lat = 24.2 + random.uniform(-0.1, 0.1)
        lon = -110.3 + random.uniform(-0.1, 0.1)
        
        for i in range(350):
            # random walk
            lat += random.normalvariate(0, 0.005)
            lon += random.normalvariate(0, 0.005)
            # Clip to Gulf limits
            lat = max(22.0, min(28.0, lat))
            lon = max(-115.0, min(-108.0, lon))
            
            speed = max(0.0, min(30.0, random.normalvariate(10.0, 2.0)))
            course = random.uniform(0.0, 360.0)
            timestamp = start_time + timedelta(seconds=i * 600)
            
            conn.execute(text("""
                INSERT INTO telemetry_records (id, vessel_id, timestamp, latitude, longitude, speed, course, is_gap_point)
                VALUES (:id, :vessel_id, :timestamp, :lat, :lon, :speed, :course, false)
            """), {
                "id": uuid.uuid4(),
                "vessel_id": v_id,
                "timestamp": timestamp,
                "lat": lat,
                "lon": lon,
                "speed": speed,
                "course": course
            })
    conn.commit()
    print("[Dataset Seed] Seeding completed successfully.")


def get_real_db_data(db_url: str):
    """
    Queries real database tables (vessels, telemetry_records, obis_occurrences, conapesca_permits)
    to build the dataset list of dicts. If data is missing or empty, it automatically seeds it.
    """
    engine = create_engine(db_url)
    
    # 1. Seeding check
    with engine.connect() as conn:
        res_vessels = conn.execute(text("SELECT count(*) FROM vessels")).fetchone()[0]
        res_telemetry = conn.execute(text("SELECT count(*) FROM telemetry_records")).fetchone()[0]
        
        if res_vessels < 5 or res_telemetry < 1000:
            seed_database_telemetry(conn)
            
    # 2. Fetch permits map
    permits_map = {}
    with engine.connect() as conn:
        res_permits = conn.execute(text("SELECT rnp, species_permit, fishing_gear_type FROM conapesca_permits")).fetchall()
        for rnp, species, gear in res_permits:
            if not rnp:
                continue
            rnp_str = str(rnp).strip()
            if rnp_str not in permits_map:
                permits_map[rnp_str] = []
            permits_map[rnp_str].append((species or "Especies", gear or "Artes de pesca"))
            
    # 3. Fetch OBIS occurrences H3 density map
    obis_map = {}
    with engine.connect() as conn:
        res_obis = conn.execute(text("SELECT h3_index, count(*) FROM obis_occurrences GROUP BY h3_index")).fetchall()
        for h3_cell, count in res_obis:
            if h3_cell:
                obis_map[h3_cell] = float(count)
                
    # 4. Fetch vessels and telemetry records
    db_vessels_data = []
    with engine.connect() as conn:
        vessels_rows = conn.execute(text("SELECT id, name, permit_id FROM vessels")).fetchall()
        
        for v_id, v_name, permit_id in vessels_rows:
            # Query telemetry
            telemetry_rows = conn.execute(text("""
                SELECT latitude, longitude, speed, course, timestamp
                FROM telemetry_records
                WHERE vessel_id = :v_id
                ORDER BY timestamp ASC
            """), {"v_id": v_id}).fetchall()
            
            if not telemetry_rows:
                continue
                
            # Build permit text description
            permit_str = str(permit_id).strip() if permit_id else None
            vessel_permits = permits_map.get(permit_str, []) if permit_str else []
            if vessel_permits:
                permit_parts = [f"{species} con {gear}" for species, gear in vessel_permits]
                permit_text = f"Permisos del buque {v_name}: " + ", ".join(permit_parts)
            else:
                permit_text = f"Buque {v_name} sin permisos registrados ante CONAPESCA"
                
            points = []
            for lat, lon, speed, course, timestamp in telemetry_rows:
                # Compute H3 cell index and get OBIS density
                h3_cell = get_h3_index(lat, lon)
                obis_density = obis_map.get(h3_cell, 0.0) if h3_cell else 0.0
                
                points.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "timestamp": timestamp.isoformat(),
                    "speed": float(speed),
                    "course": float(course),
                    "obis_density": float(obis_density)
                })
                
            db_vessels_data.append({
                "vessel_id": str(v_id),
                "permit_text": permit_text,
                "points": points
            })
            
    return db_vessels_data


def get_mock_db_data(num_vessels=5, points_per_vessel=300):
    """Generates synthetic data for testing the SSL pipeline."""
    mock_data = []
    species_list = [
        "Megaptera novaeangliae permit for scientific tagging",
        "Eubalaena japonica protection and survey permit",
        "Balaenoptera musculus research authorization",
        "Caretta caretta mitigation monitoring license",
        "Sphyrna lewini exclusion zone tracking permit"
    ]
    
    for i in range(num_vessels):
        vessel_id = f"gfw-vessel-{1000 + i}"
        points = []
        base_lat = 24.5 + np.random.uniform(-0.5, 0.5)
        base_lon = -110.2 + np.random.uniform(-0.5, 0.5)
        
        start_ts = 1704067200  # 2024-01-01 00:00:00
        
        for j in range(points_per_vessel):
            # Create a semi-continuous trajectory walk
            base_lat += np.random.normal(0, 0.01)
            base_lon += np.random.normal(0, 0.01)
            ts = start_ts + j * 600  # 10 minute intervals
            
            # Simple synthetic anomalies (spoofing check)
            if i == 0 and 100 < j < 120:
                # Add coordinate jump anomaly
                base_lat += 0.15
                
            points.append({
                "lat": base_lat,
                "lon": base_lon,
                "timestamp": datetime.fromtimestamp(ts).isoformat(),
                "speed": float(np.clip(np.random.normal(10, 2), 0, 30)),
                "course": float(np.random.uniform(0, 360)),
                "obis_density": float(np.clip(np.random.exponential(1.0), 0.0, 10.0))
            })
            
        mock_data.append({
            "vessel_id": vessel_id,
            "points": points,
            "permit_text": species_list[i % len(species_list)]
        })
        
    return mock_data

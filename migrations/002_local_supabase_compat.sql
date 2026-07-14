-- ============================================================
-- Migration: 002_local_supabase_compat
-- Purpose:   Create tables and functions locally that mimic 
--            the Supabase Cloud tables queried by the frontend APIs.
-- ============================================================

-- Ensure pgcrypto is enabled for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. static_snapshot table
CREATE TABLE IF NOT EXISTS static_snapshot (
  key        TEXT PRIMARY KEY,
  data       JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  source     TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_static_snapshot_expires_at
  ON static_snapshot (expires_at);

-- 2. gfw_cache table
CREATE TABLE IF NOT EXISTS gfw_cache (
  cache_key  TEXT PRIMARY KEY,
  data       JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gfw_cache_expires_at
  ON gfw_cache (expires_at);

-- 3. gfw_quota table
CREATE TABLE IF NOT EXISTS gfw_quota (
  today_key  TEXT PRIMARY KEY,
  count      INTEGER NOT NULL DEFAULT 1,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. missions table
CREATE TABLE IF NOT EXISTS missions (
  id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  name              TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'active', 'completed', 'cancelled')),
  priority          TEXT NOT NULL DEFAULT 'medium'
                      CHECK (priority IN ('low', 'medium', 'high', 'critical')),
  zone_name         TEXT NOT NULL,
  zone_coordinates  JSONB,          -- GeoJSON polygon or point
  assigned_to       TEXT,           -- ranger / unit name
  vessel_target     TEXT,           -- MMSI or vessel name if alert-linked
  alert_id          TEXT,           -- FK to alerts table (optional)
  notes             TEXT,
  outcome           TEXT CHECK (outcome IN ('vessel_found', 'vessel_not_found', 'intercepted', 'inconclusive')),
  outcome_notes     TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  started_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);
CREATE INDEX IF NOT EXISTS idx_missions_created_at ON missions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_missions_priority ON missions(priority);

-- 5. risk_hotspots table
CREATE TABLE IF NOT EXISTS risk_hotspots (
  id                      TEXT PRIMARY KEY,
  h3_index                TEXT NOT NULL,
  crs_level               TEXT NOT NULL DEFAULT 'LOW',
  score_acoustic          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  score_cooccurrence      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  score_traffic_density   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  estimated_spl_db        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  geom                    JSONB,
  risk_score              DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  vessel_count            INTEGER NOT NULL DEFAULT 0,
  megafauna_count         INTEGER NOT NULL DEFAULT 0,
  fishing_hours           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_risk_hotspots_h3 ON risk_hotspots(h3_index);

-- 6. megafauna table
CREATE TABLE IF NOT EXISTS megafauna (
  id            TEXT PRIMARY KEY,
  species       TEXT NOT NULL,
  taxa_group    TEXT,
  oil_relevance TEXT,
  latitude      DOUBLE PRECISION NOT NULL,
  longitude     DOUBLE PRECISION NOT NULL,
  detected_at   TIMESTAMPTZ NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_megafauna_detected_at ON megafauna(detected_at DESC);

-- 7. gap_events table
CREATE TABLE IF NOT EXISTS gap_events (
  id             TEXT PRIMARY KEY,
  vessel_mmsi    TEXT NOT NULL,
  geom           JSONB,          -- GeoJSON geometry Point
  duration_hours DOUBLE PRECISION NOT NULL,
  detected_at    TIMESTAMPTZ NOT NULL,
  risk_level     TEXT,
  mpa_proximity  TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gap_events_detected_at ON gap_events(detected_at DESC);

-- 8. increment_gfw_quota RPC function
CREATE OR REPLACE FUNCTION increment_gfw_quota(today_key TEXT, expire_time TIMESTAMPTZ)
RETURNS INTEGER AS $$
DECLARE
    current_count INTEGER;
BEGIN
    INSERT INTO gfw_quota (today_key, count, expires_at)
    VALUES (today_key, 1, expire_time)
    ON CONFLICT (today_key) DO UPDATE
    SET count = gfw_quota.count + 1
    RETURNING count INTO current_count;
    RETURN current_count;
END;
$$ LANGUAGE plpgsql;

-- 9. vedas table
CREATE TABLE IF NOT EXISTS vedas (
  id          SERIAL PRIMARY KEY,
  nombre      TEXT NOT NULL,
  tipo        TEXT NOT NULL DEFAULT 'temporal',
  mes_inicio  INTEGER NOT NULL,
  mes_fin     INTEGER NOT NULL,
  arte_pesca  TEXT NOT NULL,
  especie     TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

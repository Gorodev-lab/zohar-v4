-- ============================================================
-- Migration: 001_static_snapshot
-- Purpose:   Persistent cache for semi-static datasets.
--            Replaces repeated external API calls with a
--            72-hour materialised snapshot stored in Supabase.
-- Tables:    static_snapshot
-- Usage:     Run once against your Supabase project via the
--            SQL editor or supabase/migrations pipeline.
-- ============================================================

CREATE TABLE IF NOT EXISTS static_snapshot (
  key        TEXT PRIMARY KEY,
  data       JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  source     TEXT NOT NULL DEFAULT 'system'
);

-- Index for quick expiry checks
CREATE INDEX IF NOT EXISTS idx_static_snapshot_expires_at
  ON static_snapshot (expires_at);

COMMENT ON TABLE static_snapshot IS
  'Persistent L2 cache for semi-static datasets (vedas, megafauna, OBIS, etc.).
   Entries are refreshed every 72 hours by the application on first request after expiry.';

COMMENT ON COLUMN static_snapshot.key IS
  'Unique identifier for the snapshot (e.g. ''vedas'', ''megafauna'', ''obis_count'').';
COMMENT ON COLUMN static_snapshot.data IS
  'Serialised JSON payload — shape is specific to each key.';
COMMENT ON COLUMN static_snapshot.expires_at IS
  'UTC timestamp after which the snapshot is considered stale and will be refreshed.';
COMMENT ON COLUMN static_snapshot.source IS
  'Origin of the data (e.g. ''supabase'', ''obis'', ''manual'', ''mock'').';

-- ── Row Level Security ──────────────────────────────────────
-- Allow anonymous reads (same as the rest of the public data).
-- Writes are done server-side with the anon key + upsert header,
-- matching the existing pattern in supabase.ts (gfw_cache).

ALTER TABLE static_snapshot ENABLE ROW LEVEL SECURITY;

CREATE POLICY "public read static_snapshot"
  ON static_snapshot
  FOR SELECT
  USING (true);

CREATE POLICY "anon upsert static_snapshot"
  ON static_snapshot
  FOR INSERT
  WITH CHECK (true);

CREATE POLICY "anon update static_snapshot"
  ON static_snapshot
  FOR UPDATE
  USING (true)
  WITH CHECK (true);

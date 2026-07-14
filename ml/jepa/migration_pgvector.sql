-- Migration: Habilitar pgvector o fallbacks e incorporar embeddings de JEPA (128 dimensiones)
-- Este script configura la base de datos relacional y vectorizada.
-- Compatible con Supabase (con pgvector) y PostgreSQL local (sin pgvector como fallback).

-- 1. Habilitar la extensión pgvector o crear el dominio fallback
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION
    WHEN OTHERS THEN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
            CREATE DOMAIN vector AS float8[];
            RAISE NOTICE 'pgvector no disponible. Usando float8[] como fallback para el tipo vector.';
        END IF;
END $$;

-- 2. Asegurar que las tablas principales de LOGR_ existen
CREATE TABLE IF NOT EXISTS vessels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    permit_id TEXT,
    flag_state TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    mmsi TEXT UNIQUE,
    shipname TEXT,
    flag TEXT,
    vessel_type TEXT,
    detected_at TIMESTAMPTZ,
    geom JSONB,
    source TEXT,
    anomaly INTEGER DEFAULT 0,
    collision_risk_prob DOUBLE PRECISION DEFAULT 0.05,
    collision_risk_level TEXT DEFAULT 'LOW'
);

CREATE TABLE IF NOT EXISTS telemetry_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vessel_id UUID REFERENCES vessels(id) ON DELETE CASCADE,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    speed DOUBLE PRECISION NOT NULL,
    course DOUBLE PRECISION NOT NULL,
    is_gap_point BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS traffic_anomalies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vessel_id UUID REFERENCES vessels(id) ON DELETE CASCADE,
    anomaly_type TEXT NOT NULL, -- p.ej. 'AIS Gap', 'Speed Anomaly'
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE,
    start_lat DOUBLE PRECISION,
    start_lon DOUBLE PRECISION,
    end_lat DOUBLE PRECISION,
    end_lon DOUBLE PRECISION,
    description TEXT,
    -- Columna para almacenar el embedding de comportamiento latente de 128 dimensiones predicho por JEPA
    jepa_behavior_embedding vector
);

CREATE TABLE IF NOT EXISTS regulatory_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_code TEXT UNIQUE NOT NULL, -- p.ej. 'NOM-002', 'VEDA-CAMARON'
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    applicable_polygon TEXT -- Polígono H3 o delimitación geográfica
);

-- Tabla para el Grafo Relacional (Alternativa limpia a Kùzu DB)
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL,
    source_type TEXT NOT NULL, -- 'vessels', 'traffic_anomalies', 'regulatory_rules'
    target_id UUID NOT NULL,
    target_type TEXT NOT NULL,
    relationship_type TEXT NOT NULL, -- p.ej. 'LOCATED_IN', 'VIOLATES', 'ASSOCIATED_WITH'
    source_citation TEXT, -- Linaje de datos (Cita autoritativa o procedencia)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 3. Crear índices para búsqueda de similitud coseno rápida
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'traffic_anomalies_jepa_behavior_embedding_idx') THEN
            CREATE INDEX ON traffic_anomalies USING hnsw (jepa_behavior_embedding vector_cosine_ops);
        END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'traffic_anomalies_jepa_behavior_embedding_idx') THEN
            CREATE INDEX ON traffic_anomalies (jepa_behavior_embedding);
        END IF;
    END IF;
END $$;

-- 4. Función de fallback para cálculo de similitud coseno con float8[]
CREATE OR REPLACE FUNCTION cosine_similarity_fallback(a float8[], b float8[])
RETURNS float8 AS $$
DECLARE
    dot_product float8 := 0;
    norm_a float8 := 0;
    norm_b float8 := 0;
    i int;
BEGIN
    IF a IS NULL OR b IS NULL OR array_length(a, 1) IS NULL OR array_length(b, 1) IS NULL THEN
        RETURN 0;
    END IF;
    FOR i IN 1..array_length(a, 1) LOOP
        dot_product := dot_product + (a[i] * b[i]);
        norm_a := norm_a + (a[i] * a[i]);
        norm_b := norm_b + (b[i] * b[i]);
    END LOOP;
    IF norm_a = 0 OR norm_b = 0 THEN
        RETURN 0;
    END IF;
    RETURN dot_product / (sqrt(norm_a) * sqrt(norm_b));
END;
$$ LANGUAGE plpgsql;

-- 5. Función SQL en Supabase para búsqueda semántica híbrida de anomalías por similitud de comportamiento
CREATE OR REPLACE FUNCTION match_jepa_anomalies (
    query_embedding vector,
    match_threshold float,
    match_count int
)
RETURNS TABLE (
    id UUID,
    vessel_name TEXT,
    anomaly_type TEXT,
    description TEXT,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RETURN QUERY
        SELECT
            ta.id,
            v.name AS vessel_name,
            ta.anomaly_type,
            ta.description,
            (1.0 - (ta.jepa_behavior_embedding <=> query_embedding))::float AS similarity
        FROM traffic_anomalies ta
        JOIN vessels v ON ta.vessel_id = v.id
        WHERE (1.0 - (ta.jepa_behavior_embedding <=> query_embedding)) > match_threshold
        ORDER BY ta.jepa_behavior_embedding <=> query_embedding
        LIMIT match_count;
    ELSE
        RETURN QUERY
        SELECT
            ta.id,
            v.name AS vessel_name,
            ta.anomaly_type,
            ta.description,
            cosine_similarity_fallback(ta.jepa_behavior_embedding::float8[], query_embedding::float8[]) AS similarity
        FROM traffic_anomalies ta
        JOIN vessels v ON ta.vessel_id = v.id
        WHERE cosine_similarity_fallback(ta.jepa_behavior_embedding::float8[], query_embedding::float8[]) > match_threshold
        ORDER BY cosine_similarity_fallback(ta.jepa_behavior_embedding::float8[], query_embedding::float8[]) DESC
        LIMIT match_count;
    END IF;
END;
$$;

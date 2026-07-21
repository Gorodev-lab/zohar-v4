-- Schema initialization for Zohar SEMARNAT Data Warehouse
-- Supports local PostgreSQL and Supabase

-- 1. SEMARNAT Environmental Projects Table
CREATE TABLE IF NOT EXISTS public.semarnat_projects (
    clave VARCHAR(50) PRIMARY KEY,
    project_name TEXT,
    status VARCHAR(255),
    sector VARCHAR(255),
    state VARCHAR(255),
    year INT,
    files_downloaded TEXT[],
    promovente TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indices on sector, state, year
CREATE INDEX IF NOT EXISTS idx_semarnat_sector ON public.semarnat_projects(sector);
CREATE INDEX IF NOT EXISTS idx_semarnat_state ON public.semarnat_projects(state);
CREATE INDEX IF NOT EXISTS idx_semarnat_year ON public.semarnat_projects(year);

-- 2. SEMARNAT AI Evaluations Table (Inferences)
CREATE TABLE IF NOT EXISTS public.project_evaluations (
    clave VARCHAR(50) PRIMARY KEY REFERENCES public.semarnat_projects(clave) ON DELETE CASCADE,
    veredicto VARCHAR(50),
    score DOUBLE PRECISION,
    confianza_pct INT,
    knockouts JSONB,
    yes_signals JSONB,
    no_signals JSONB,
    condicionantes JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evaluations_veredicto ON public.project_evaluations(veredicto);

-- 3. Download Verification Manifest Table (Pre-Extraction Gate)
CREATE TABLE IF NOT EXISTS public.download_manifest (
    clave VARCHAR(50),
    file_type VARCHAR(20),
    file_path TEXT PRIMARY KEY,
    sha256 VARCHAR(64),
    file_size BIGINT,
    page_count INT,
    status VARCHAR(20),
    verified_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manifest_status ON public.download_manifest(status);
CREATE INDEX IF NOT EXISTS idx_manifest_clave ON public.download_manifest(clave);

-- 4. Vector Document Embeddings Table (Phase 6 RAG Engine)
CREATE TABLE IF NOT EXISTS public.document_embeddings (
    id SERIAL PRIMARY KEY,
    clave VARCHAR(50),
    section_title TEXT,
    chunk_text TEXT,
    embedding JSONB,
    sha256 VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_embeddings_clave ON public.document_embeddings(clave);



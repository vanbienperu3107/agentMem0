-- Migration v2 — add LLM summaries, embeddings, R2 storage support
-- Apply AFTER schema.sql:
--   docker exec -i memory-postgres psql -U mem0 mem0 < schema-v2.sql
--
-- Backward compatible: all new columns are nullable.

-- ----- chat_sessions additions -----

-- LLM-generated summary (replaces first_user[:200] fallback)
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS llm_summary TEXT;

-- Pointer to Qdrant point (for semantic search on llm_summary)
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS embedding_id UUID;

-- R2/B2 storage pointer (when transcript moved to object storage)
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS r2_key TEXT;
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS r2_size_bytes BIGINT;

-- After migration completes and verified, drop the big JSONB column:
-- (DO THIS MANUALLY, only when r2_key populated for all rows)
-- ALTER TABLE chat_sessions DROP COLUMN transcript;

-- ----- index for llm_summary search -----
CREATE INDEX IF NOT EXISTS idx_sessions_llm_summary_trgm
    ON chat_sessions USING gin (llm_summary gin_trgm_ops);

-- ----- ingestion tracking -----
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,  -- 'summarize' | 'embed' | 'r2_upload'
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    attempts INT DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_type
    ON ingestion_jobs (status, job_type);

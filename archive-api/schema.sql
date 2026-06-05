-- Postgres schema for archive-api
-- Run inside the postgres container:
--   docker exec -i memory-postgres psql -U mem0 mem0 < schema.sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    project_tag TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    message_count INT,
    transcript JSONB NOT NULL,
    summary TEXT,
    workspace_path TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_date
    ON chat_sessions (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project
    ON chat_sessions (project_tag);
CREATE INDEX IF NOT EXISTS idx_sessions_summary_trgm
    ON chat_sessions USING gin (summary gin_trgm_ops);

CREATE TABLE IF NOT EXISTS compact_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    compacted_at TIMESTAMPTZ DEFAULT NOW(),
    summary_text TEXT NOT NULL,
    messages_before INT DEFAULT 0,
    position_in_session INT,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_compact_user_date
    ON compact_summaries (user_id, compacted_at DESC);
CREATE INDEX IF NOT EXISTS idx_compact_summary_trgm
    ON compact_summaries USING gin (summary_text gin_trgm_ops);

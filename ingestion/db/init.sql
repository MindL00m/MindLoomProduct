-- Loom — PostgreSQL schema.
-- Run against the target database before starting the service.

CREATE EXTENSION IF NOT EXISTS vector;

-- --- Tenancy -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS organizations (
    org_id     TEXT PRIMARY KEY,
    name       TEXT        NOT NULL,
    domain     TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT PRIMARY KEY,
    org_id     TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    email      TEXT        NOT NULL UNIQUE,
    google_sub TEXT,
    name       TEXT,
    photo_url  TEXT,
    role       TEXT        NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_org_id ON users (org_id);

-- --- Connected apps (per user, org-scoped) --------------------------------

CREATE TABLE IF NOT EXISTS app_connections (
    connection_id  TEXT PRIMARY KEY,
    org_id         TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id        TEXT        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider       TEXT        NOT NULL,
    account_email  TEXT,
    access_token   TEXT        NOT NULL,
    refresh_token  TEXT,
    token_expiry   TIMESTAMPTZ,
    scopes         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_app_connections_org_user ON app_connections (org_id, user_id);

-- --- Incremental Google Workspace sync cursors ----------------------------

CREATE TABLE IF NOT EXISTS sync_cursors (
    cursor_id        TEXT PRIMARY KEY,
    org_id           TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id          TEXT        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider         TEXT        NOT NULL,
    account_email    TEXT        NOT NULL,
    cursor_value     TEXT,
    watch_resource   TEXT,
    watch_expiration TIMESTAMPTZ,
    status           TEXT        NOT NULL DEFAULT 'active',
    last_synced_at   TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_sync_cursors_org_provider ON sync_cursors (org_id, provider);

-- --- Chunks (org-scoped) -------------------------------------------------

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id          TEXT PRIMARY KEY,
    org_id            TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    raw_text          TEXT        NOT NULL,
    start_time        TIMESTAMPTZ NOT NULL,
    end_time          TIMESTAMPTZ NOT NULL,
    speakers          TEXT[]      NOT NULL,
    knowledge_type    TEXT        NOT NULL,
    confidence        TEXT        NOT NULL,
    confidence_reason TEXT        NOT NULL,
    summary           TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_org_id ON chunks (org_id);
CREATE INDEX IF NOT EXISTS idx_chunks_knowledge_type ON chunks (knowledge_type);
CREATE INDEX IF NOT EXISTS idx_chunks_start_time ON chunks (start_time);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id  TEXT PRIMARY KEY REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    embedding VECTOR(1536) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_cosine
    ON chunk_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- --- Conversations (org-scoped) ------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id   TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    source            TEXT NOT NULL,
    title             TEXT,
    participant_count INTEGER,
    message_count     INTEGER,
    ingested_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_org_id ON conversations (org_id);

-- --- Ingestion jobs (org-scoped) -----------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id          TEXT PRIMARY KEY,
    org_id          TEXT REFERENCES organizations (org_id) ON DELETE CASCADE,
    conversation_id TEXT,
    status          TEXT NOT NULL,
    progress        TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_org_id ON ingestion_jobs (org_id);

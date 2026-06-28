-- Company Brain — PostgreSQL schema for WhatsApp ingestion.
-- Run against the target database before starting the service.

-- Required for vector storage / similarity search.
CREATE EXTENSION IF NOT EXISTS vector;

-- Raw chunk text and extracted metadata.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id          TEXT PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS idx_chunks_knowledge_type ON chunks (knowledge_type);
CREATE INDEX IF NOT EXISTS idx_chunks_start_time ON chunks (start_time);

-- Embedding vectors (text-embedding-3-small -> 1536 dimensions).
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id  TEXT PRIMARY KEY REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    embedding VECTOR(1536) NOT NULL
);

-- Approximate nearest-neighbour index for cosine similarity search.
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_cosine
    ON chunk_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- One row per ingested conversation (any connector: whatsapp, teams, slack).
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id   TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    title             TEXT,
    participant_count INTEGER,
    message_count     INTEGER,
    ingested_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Persisted ingestion job state (allows recovery across restarts).
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id          TEXT PRIMARY KEY,
    conversation_id TEXT,
    status          TEXT NOT NULL,
    progress        TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

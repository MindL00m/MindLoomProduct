CREATE TABLE IF NOT EXISTS external_sources (
    source_key       TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    provider         TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    version          TEXT,
    content_hash     TEXT,
    document_id      TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    visible_to_json  TEXT NOT NULL DEFAULT '[]',
    last_seen_at     TIMESTAMPTZ NOT NULL,
    deleted_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_external_source_identity UNIQUE (org_id, provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_external_sources_provider
    ON external_sources (org_id, provider, status);

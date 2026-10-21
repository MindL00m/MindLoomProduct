-- Files that keep themselves current from newly ingested knowledge.
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS monitor_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS monitor_instructions TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS monitor_keywords TEXT NOT NULL DEFAULT '[]';
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS monitor_embedding TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS monitor_started_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_loombot_files_monitored
  ON loombot_files (org_id) WHERE monitor_enabled;
CREATE TABLE IF NOT EXISTS loombot_file_seen (
    file_id   TEXT NOT NULL REFERENCES loombot_files (file_id) ON DELETE CASCADE,
    chunk_id  TEXT NOT NULL,
    seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS loombot_file_events (
    event_id         TEXT PRIMARY KEY,
    file_id          TEXT NOT NULL REFERENCES loombot_files (file_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL,
    summary          TEXT NOT NULL,
    rows_added       INTEGER NOT NULL DEFAULT 0,
    cells_updated    INTEGER NOT NULL DEFAULT 0,
    source_chunk_ids TEXT NOT NULL DEFAULT '[]',
    source_label     TEXT,
    source_url       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_loombot_file_events_file
  ON loombot_file_events (file_id, created_at DESC);

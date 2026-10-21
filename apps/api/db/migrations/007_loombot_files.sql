-- Per-user CSV files for the LoomBot Files tab, editable from Files and Ask.
CREATE TABLE IF NOT EXISTS loombot_files (
    file_id          TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    owner_user_id    TEXT NOT NULL,
    name             TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    previous_content TEXT,
    row_count        INTEGER NOT NULL DEFAULT 0,
    column_count     INTEGER NOT NULL DEFAULT 0,
    last_edited_by   TEXT NOT NULL DEFAULT 'user'
        CHECK (last_edited_by IN ('user', 'loombot')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_loombot_files_owner_name
  ON loombot_files (org_id, owner_user_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_loombot_files_owner_updated
  ON loombot_files (org_id, owner_user_id, updated_at DESC);

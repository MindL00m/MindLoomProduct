-- Ask-requested status trackers and the history of their updates.
CREATE TABLE IF NOT EXISTS status_trackers (
    tracker_id      TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    owner_user_id   TEXT NOT NULL,
    title           TEXT NOT NULL,
    topic           TEXT NOT NULL,
    keywords        TEXT NOT NULL DEFAULT '[]',
    embedding       TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('on_track', 'at_risk', 'blocked', 'done', 'unknown')),
    summary         TEXT NOT NULL DEFAULT '',
    archived        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_update_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_status_trackers_org_active
  ON status_trackers (org_id, archived);
CREATE INDEX IF NOT EXISTS idx_status_trackers_owner
  ON status_trackers (org_id, owner_user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS status_tracker_updates (
    update_id        TEXT PRIMARY KEY,
    tracker_id       TEXT NOT NULL REFERENCES status_trackers (tracker_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('created', 'history', 'update')),
    summary          TEXT NOT NULL,
    status           TEXT,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_chunk_ids TEXT NOT NULL DEFAULT '[]',
    source_label     TEXT,
    source_url       TEXT,
    document_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_status_tracker_updates_tracker
  ON status_tracker_updates (tracker_id, occurred_at DESC);
CREATE TABLE IF NOT EXISTS status_tracker_seen (
    tracker_id  TEXT NOT NULL REFERENCES status_trackers (tracker_id) ON DELETE CASCADE,
    chunk_id    TEXT NOT NULL,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tracker_id, chunk_id)
);

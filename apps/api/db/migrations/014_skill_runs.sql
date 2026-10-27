-- Skill-file execution: page anchors recorded with browser captures, and runs of an
-- approved skill walked by the Mac app and the Chrome extension.
ALTER TABLE captures ADD COLUMN IF NOT EXISTS browser_anchor_json TEXT;

CREATE TABLE IF NOT EXISTS skill_runs (
    run_id       TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    skill_id     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'waiting', 'completed', 'stopped', 'failed')),
    step_index   INTEGER NOT NULL DEFAULT 0 CHECK (step_index >= 0),
    wait_reason  TEXT
        CHECK (wait_reason IN ('no_anchor', 'needs_value', 'action_failed', 'agent_offline')),
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_runs_user_status
  ON skill_runs (org_id, user_id, status, created_at DESC);

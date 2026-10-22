-- Recurring email workflows and their send history.
CREATE TABLE IF NOT EXISTS recurring_emails (
    workflow_id           TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    owner_user_id         TEXT NOT NULL,
    name                  TEXT NOT NULL,
    recipient_email       TEXT NOT NULL,
    recipient_name        TEXT,
    subject               TEXT NOT NULL,
    body                  TEXT NOT NULL DEFAULT '',
    refresh_instructions  TEXT,
    interval_value        INTEGER NOT NULL,
    interval_unit         TEXT NOT NULL CHECK (interval_unit IN ('minutes', 'hours', 'days', 'weeks')),
    interval_seconds      INTEGER NOT NULL CHECK (interval_seconds >= 900),
    timezone              TEXT,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error')),
    next_run_at           TIMESTAMPTZ NOT NULL,
    last_run_at           TIMESTAMPTZ,
    run_count             INTEGER NOT NULL DEFAULT 0,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recurring_emails_due
  ON recurring_emails (next_run_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_recurring_emails_owner
  ON recurring_emails (org_id, owner_user_id);
CREATE TABLE IF NOT EXISTS recurring_email_runs (
    run_id        TEXT PRIMARY KEY,
    workflow_id   TEXT NOT NULL REFERENCES recurring_emails (workflow_id) ON DELETE CASCADE,
    org_id        TEXT NOT NULL,
    ran_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    error         TEXT,
    subject       TEXT NOT NULL DEFAULT '',
    body_preview  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_recurring_email_runs_workflow
  ON recurring_email_runs (workflow_id, ran_at DESC);

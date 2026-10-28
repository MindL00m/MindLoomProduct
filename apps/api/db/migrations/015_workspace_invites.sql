-- People invited to a workspace by email before they have signed in.
-- Accepted automatically on their first sign-in (keeper/auth.google_signin).
CREATE TABLE IF NOT EXISTS workspace_invites (
    invite_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    invited_by TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at TIMESTAMPTZ,
    UNIQUE (workspace_id, email)
);
CREATE INDEX IF NOT EXISTS idx_workspace_invites_pending
  ON workspace_invites (org_id, email) WHERE accepted_at IS NULL;

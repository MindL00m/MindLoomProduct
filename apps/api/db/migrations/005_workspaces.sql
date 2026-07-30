-- Group chat workspaces (org-wide Everyone room + custom rooms)
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'group',
    created_by TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_org_wide
  ON workspaces (org_id) WHERE kind = 'org_wide';

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_user
  ON workspace_members (user_id, workspace_id);

CREATE TABLE IF NOT EXISTS workspace_messages (
    message_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    sender_user_id TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    sender_type TEXT NOT NULL DEFAULT 'user',
    sender_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workspace_messages_thread
  ON workspace_messages (workspace_id, created_at);

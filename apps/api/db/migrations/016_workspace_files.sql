-- Files shared into a workspace. The file stays in its owner's Files; every member
-- can open and edit the same file through the workspace, so edits are shared.
CREATE TABLE IF NOT EXISTS workspace_files (
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    added_by TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_files_file ON workspace_files (file_id);

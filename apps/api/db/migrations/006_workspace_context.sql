-- Workspace CONTEXT.md for Loombot context-only project rooms
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS purpose TEXT;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS context_md TEXT;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS context_synced_at TIMESTAMPTZ;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS loombot_mode TEXT NOT NULL DEFAULT 'org_knowledge';

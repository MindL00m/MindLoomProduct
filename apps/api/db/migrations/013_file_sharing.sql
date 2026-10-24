-- "Share in MindLoom": a shared file is copied into the recipient's Files with who
-- shared it; share_seen_at stays empty until the recipient sees the notification.
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS shared_by_user_id TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS shared_by_name TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS shared_note TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS shared_at TIMESTAMPTZ;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS share_seen_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_loombot_files_unseen_shares
  ON loombot_files (org_id, owner_user_id) WHERE shared_at IS NOT NULL AND share_seen_at IS NULL;

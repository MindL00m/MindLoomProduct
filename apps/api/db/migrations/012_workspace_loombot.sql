-- Workspace Loombot replies carry Ask's full response so workspaces get the same
-- cards as Ask (emails, meetings, files, status trackers, web results).
ALTER TABLE workspace_messages ADD COLUMN IF NOT EXISTS payload TEXT;
-- The member who asked Loombot; only they can act on the reply's cards.
ALTER TABLE workspace_messages ADD COLUMN IF NOT EXISTS requested_by TEXT;
-- What that member did on the cards. Other members see only the outcomes
-- (e.g. "meeting scheduled"), never the cards' contents.
ALTER TABLE workspace_messages ADD COLUMN IF NOT EXISTS card_state TEXT;

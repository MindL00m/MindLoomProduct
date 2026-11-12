-- Live WhatsApp chats forwarded by a user's own OpenClaw gateway
-- (weaver/whatsapp_live.py). One link token per user, stored as a hash.
CREATE TABLE IF NOT EXISTS whatsapp_live_links (
    link_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    UNIQUE (org_id, user_id)
);
-- Messages wait here until their chat goes quiet, then are ingested per chat-day.
CREATE TABLE IF NOT EXISTS whatsapp_live_messages (
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    chat_name TEXT,
    sender_id TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL,
    body TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at TIMESTAMPTZ,
    PRIMARY KEY (org_id, user_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_live_pending
  ON whatsapp_live_messages (received_at) WHERE ingested_at IS NULL;

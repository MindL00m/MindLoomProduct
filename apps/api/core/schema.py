"""Apply the base Postgres schema, then idempotent upgrades for older databases."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text

from core.database import get_session_factory

logger = logging.getLogger(__name__)

_INIT_SQL_PATH = Path(__file__).resolve().parent.parent / "db" / "init.sql"
_SCHEMA_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('mindloom_schema'))"


def _sql_statements(sql: str) -> list[str]:
    """Split a SQL file into executable statements, ignoring line comments."""

    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements

_APP_CONNECTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_connections (
    connection_id  TEXT PRIMARY KEY,
    org_id         TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id        TEXT        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider       TEXT        NOT NULL,
    account_email  TEXT,
    access_token   TEXT        NOT NULL,
    refresh_token  TEXT,
    token_expiry   TIMESTAMPTZ,
    scopes         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
)
"""

_APP_CONNECTIONS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_app_connections_org_user ON app_connections (org_id, user_id)
"""

_SYNC_CURSORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sync_cursors (
    cursor_id        TEXT PRIMARY KEY,
    org_id           TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id          TEXT        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider         TEXT        NOT NULL,
    account_email    TEXT        NOT NULL,
    cursor_value     TEXT,
    watch_resource   TEXT,
    watch_expiration TIMESTAMPTZ,
    status           TEXT        NOT NULL DEFAULT 'active',
    last_synced_at   TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
)
"""

_SYNC_CURSORS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_sync_cursors_org_provider ON sync_cursors (org_id, provider)
"""

_CONNECTION_POLICIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS connection_policies (
    policy_id             TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id               TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider              TEXT NOT NULL,
    included_resources    TEXT NOT NULL,
    excluded_resources    TEXT NOT NULL DEFAULT '[]',
    include_history       BOOLEAN NOT NULL DEFAULT TRUE,
    history_start_date    TEXT,
    sync_frequency        TEXT NOT NULL DEFAULT 'realtime',
    access_mode           TEXT NOT NULL DEFAULT 'respect_source_permissions',
    allowed_departments   TEXT NOT NULL DEFAULT '[]',
    allowed_user_ids      TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'setup_required',
    last_synced_at        TIMESTAMPTZ,
    last_error            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
)
"""

_CONNECTION_POLICIES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_connection_policies_org_provider
ON connection_policies (org_id, provider)
"""

_LLM_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS organization_llm_settings (
    org_id          TEXT PRIMARY KEY REFERENCES organizations (org_id) ON DELETE CASCADE,
    provider        TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'local')),
    base_url        TEXT,
    model           TEXT,
    context_window  INTEGER NOT NULL DEFAULT 16384 CHECK (context_window >= 1024),
    supports_tools  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CHUNKS_VISIBILITY_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS visible_to TEXT[] NOT NULL DEFAULT '{}'
"""

_DURABLE_INTEGRATIONS_SQL = [
"""ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS result_json TEXT""",
"""CREATE TABLE IF NOT EXISTS integration_subscriptions (
    subscription_key TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    resource_id TEXT,
    expiration TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_integration_subscription_resource
      UNIQUE (org_id, user_id, provider, resource)
)""",
"""CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_subscriptions_external
  ON integration_subscriptions (provider, external_id)""",
"""CREATE TABLE IF NOT EXISTS external_sources (
    source_key TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    version TEXT,
    content_hash TEXT,
    document_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    visible_to_json TEXT NOT NULL DEFAULT '[]',
    last_seen_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_external_source_identity UNIQUE (org_id, provider, external_id)
)""",
"""CREATE TABLE IF NOT EXISTS knowledge_reviews (
    review_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    review_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_by TEXT,
    owner_user_id TEXT,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    proposed_content TEXT,
    due_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution_note TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_queue
  ON knowledge_reviews (org_id, review_type, status, due_at)""",
"""CREATE TABLE IF NOT EXISTS knowledge_claims (
    claim_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, chunk_id, claim_text)
)""",
"""CREATE TABLE IF NOT EXISTS sync_runs (
    run_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    imported INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_sync_runs_org_provider
  ON sync_runs (org_id, provider, started_at DESC)""",
"""CREATE TABLE IF NOT EXISTS knowledge_review_schedules (
    schedule_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    interval_days INTEGER NOT NULL DEFAULT 180,
    next_review_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, source_id)
)""",
"""CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    review_id TEXT NOT NULL REFERENCES knowledge_reviews (review_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_message_id TEXT,
    error TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    UNIQUE (review_id, channel)
)""",
"""CREATE INDEX IF NOT EXISTS idx_notification_deliveries_review
  ON notification_deliveries (org_id, review_id, channel)""",
"""CREATE TABLE IF NOT EXISTS expert_messages (
    message_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    review_id TEXT NOT NULL REFERENCES knowledge_reviews (review_id) ON DELETE CASCADE,
    sender_user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    attachment_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at TIMESTAMPTZ
)""",
"""CREATE INDEX IF NOT EXISTS idx_expert_messages_thread
  ON expert_messages (org_id, review_id, created_at)""",
"""CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'group',
    created_by TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    purpose TEXT,
    context_md TEXT,
    context_synced_at TIMESTAMPTZ,
    loombot_mode TEXT NOT NULL DEFAULT 'org_knowledge',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_org_wide
  ON workspaces (org_id) WHERE kind = 'org_wide'""",
"""ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS purpose TEXT""",
"""ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS context_md TEXT""",
"""ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS context_synced_at TIMESTAMPTZ""",
"""ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS loombot_mode TEXT NOT NULL DEFAULT 'org_knowledge'""",
"""CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
)""",
"""CREATE INDEX IF NOT EXISTS idx_workspace_members_user
  ON workspace_members (user_id, workspace_id)""",
"""CREATE TABLE IF NOT EXISTS workspace_messages (
    message_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    sender_user_id TEXT REFERENCES users (user_id) ON DELETE SET NULL,
    sender_type TEXT NOT NULL DEFAULT 'user',
    sender_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_workspace_messages_thread
  ON workspace_messages (workspace_id, created_at)""",
"""CREATE TABLE IF NOT EXISTS captures (
    capture_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    tab_title TEXT NOT NULL DEFAULT '',
    window_id INTEGER,
    blob_key TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    redactions_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_captures_org_session
  ON captures (org_id, session_id, timestamp_ms)""",
"""CREATE TABLE IF NOT EXISTS capture_summaries (
    capture_id TEXT PRIMARY KEY REFERENCES captures (capture_id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_capture_summaries_org
  ON capture_summaries (org_id, created_at DESC)""",
"""CREATE TABLE IF NOT EXISTS activity_sessions (
    session_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_activity_sessions_org
  ON activity_sessions (org_id, received_at DESC)""",
"""CREATE TABLE IF NOT EXISTS skill_files (
    skill_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
"""CREATE INDEX IF NOT EXISTS idx_skill_files_org
  ON skill_files (org_id, updated_at DESC)""",
"""CREATE TABLE IF NOT EXISTS slack_installations (
    install_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL UNIQUE REFERENCES organizations (org_id) ON DELETE CASCADE,
    team_id TEXT UNIQUE,
    team_name TEXT,
    bot_user_id TEXT,
    app_id TEXT,
    bot_token_enc TEXT,
    signing_secret_enc TEXT,
    scopes TEXT NOT NULL DEFAULT '',
    ingest_channel_ids TEXT NOT NULL DEFAULT '[]',
    ingest_mention_threads BOOLEAN NOT NULL DEFAULT TRUE,
    answer_mentions BOOLEAN NOT NULL DEFAULT TRUE,
    unlinked_user_policy TEXT NOT NULL DEFAULT 'ignore'
        CHECK (unlinked_user_policy IN ('ignore', 'org')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'error')),
    last_event_at TIMESTAMPTZ,
    last_error TEXT,
    installed_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
]


# Per-user CSV files for the LoomBot Files tab (see loombot/files.py).
_LOOMBOT_FILES_SQL = [
    """CREATE TABLE IF NOT EXISTS loombot_files (
    file_id          TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    owner_user_id    TEXT NOT NULL,
    name             TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    previous_content TEXT,
    row_count        INTEGER NOT NULL DEFAULT 0,
    column_count     INTEGER NOT NULL DEFAULT 0,
    last_edited_by   TEXT NOT NULL DEFAULT 'user'
        CHECK (last_edited_by IN ('user', 'loombot')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_loombot_files_owner_name
  ON loombot_files (org_id, owner_user_id, lower(name))""",
    """CREATE INDEX IF NOT EXISTS idx_loombot_files_owner_updated
  ON loombot_files (org_id, owner_user_id, updated_at DESC)""",
]


async def ensure_schema() -> None:
    """Create the base schema on an empty database, then apply additive upgrades."""

    session_factory = get_session_factory()
    init_statements = _sql_statements(_INIT_SQL_PATH.read_text(encoding="utf-8"))
    async with session_factory() as session:
        async with session.begin():
            # Railway starts the API and worker independently. Serialize their
            # first-run DDL so both cannot race to create the same base tables.
            await session.execute(text(_SCHEMA_LOCK_SQL))
            for statement in init_statements:
                await session.execute(text(statement))
            await session.execute(text(_APP_CONNECTIONS_TABLE_SQL))
            await session.execute(text(_APP_CONNECTIONS_INDEX_SQL))
            await session.execute(text(_SYNC_CURSORS_TABLE_SQL))
            await session.execute(text(_SYNC_CURSORS_INDEX_SQL))
            await session.execute(text(_CONNECTION_POLICIES_TABLE_SQL))
            await session.execute(text(_CONNECTION_POLICIES_INDEX_SQL))
            await session.execute(text(_LLM_SETTINGS_TABLE_SQL))
            await session.execute(text(_CHUNKS_VISIBILITY_SQL))
            for statement in _DURABLE_INTEGRATIONS_SQL:
                await session.execute(text(statement))
            for statement in _LOOMBOT_FILES_SQL:
                await session.execute(text(statement))
    logger.info("Schema check complete (base tables, connections, captures, policies)")

-- Apply this to databases created before integration_subscriptions was added.
CREATE TABLE IF NOT EXISTS integration_subscriptions (
    subscription_key TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider          TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    resource          TEXT NOT NULL,
    resource_id       TEXT,
    expiration        TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_integration_subscription_resource
        UNIQUE (org_id, user_id, provider, resource)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_subscriptions_external
    ON integration_subscriptions (provider, external_id);
CREATE INDEX IF NOT EXISTS idx_integration_subscriptions_expiration
    ON integration_subscriptions (provider, expiration);

-- Bring-your-own AI: an organization can connect Claude, and save its own
-- provider API key (stored encrypted by core.token_crypto).
ALTER TABLE organization_llm_settings ADD COLUMN IF NOT EXISTS api_key TEXT;
ALTER TABLE organization_llm_settings DROP CONSTRAINT IF EXISTS organization_llm_settings_provider_check;
ALTER TABLE organization_llm_settings ADD CONSTRAINT organization_llm_settings_provider_check
  CHECK (provider IN ('openai', 'anthropic', 'local'));

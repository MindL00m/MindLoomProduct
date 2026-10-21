"""Application configuration sourced from environment variables.

This module is the single permitted location for global mutable-ish state
(the cached :class:`Settings` instance). Everything else in the pipeline reads
configuration through :func:`get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application settings loaded from the environment / ``.env``.

    Field names map case-insensitively to environment variable names, so the
    ``openai_api_key`` field is populated from ``OPENAI_API_KEY``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: str = Field(..., description="API key used for OpenAI embeddings and chat completions.")
    local_llm_api_key: str = Field(
        default="local",
        description="Optional bearer token for an OpenAI-compatible local LLM endpoint.",
    )
    local_llm_timeout_seconds: float = Field(default=60.0, gt=0)
    postgres_url: str = Field(..., description="SQLAlchemy connection URL for the PostgreSQL/pgvector database.")
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis used by the durable ingestion worker queue.",
    )
    neo4j_uri: str = Field(..., description="Bolt URI of the Neo4j instance, e.g. bolt://localhost:7687.")
    neo4j_username: str = Field(..., description="Username for authenticating against Neo4j.")
    neo4j_password: str = Field(..., description="Password for authenticating against Neo4j.")

    app_env: Literal["development", "production"] = Field(
        default="development",
        description="Runtime environment. Production disables connect-dev and requires SESSION_SECRET.",
    )
    session_secret: str = Field(
        default="",
        description=(
            "HMAC secret used to sign MindLoom access JWTs. Required when APP_ENV=production; "
            "a fixed insecure default is used in development if left empty."
        ),
    )
    session_ttl_hours: int = Field(
        default=168,
        description="Lifetime of issued MindLoom access JWTs in hours (default 7 days).",
    )
    allow_dev_integrations: bool = Field(
        default=False,
        description=(
            "When true and APP_ENV=development, allow connect-dev fake workspace connections. "
            "Ignored (always false) in production."
        ),
    )

    chunk_gap_minutes: int = Field(
        30,
        description="Minutes of silence between two messages that forces a new chunk boundary.",
    )
    chunk_max_tokens: int = Field(
        800,
        description="Maximum number of cl100k_base tokens allowed in a single chunk.",
    )
    speaker_similarity_threshold: int = Field(
        85,
        description="Fuzzy match score (0-100) at or above which two speaker names are merged.",
    )
    retrieval_similarity_threshold: float = Field(
        0.3,
        description=(
            "Minimum cosine similarity (0-1) a chunk must reach to be returned by "
            "vector search. Tuned for text-embedding-3-small, whose relevant matches "
            "on short conversational text typically score ~0.3-0.5."
        ),
    )
    retrieval_chunk_limit: int = Field(
        5,
        description="Maximum number of chunks returned by a single vector search.",
    )

    openai_request_timeout_seconds: float = Field(
        30.0,
        description="Hard timeout applied to every outbound OpenAI API request.",
    )

    blob_storage_backend: Literal["local", "s3"] = Field(
        "local",
        description=(
            "Blob storage backend for raw uploaded files and capture images. "
            "Use 's3' for multi-replica / durable object storage."
        ),
    )
    blob_storage_root: str = Field(
        "./data/blobs",
        description="Filesystem root for the local blob storage backend.",
    )
    s3_bucket: str = Field(
        default="",
        description="S3 bucket name when BLOB_STORAGE_BACKEND=s3.",
    )
    s3_region: str = Field(
        default="us-east-1",
        description="AWS region for S3 blob storage.",
    )
    s3_endpoint_url: str = Field(
        default="",
        description=(
            "Optional custom S3 endpoint (MinIO, Cloudflare R2). "
            "Leave empty for AWS."
        ),
    )
    capture_storage_root: str = Field(
        "./data/captures",
        description=(
            "Legacy local root for capture JSONL/images (read shim only). "
            "New captures use Postgres + blob storage."
        ),
    )
    openai_default_model: str = Field(
        "gpt-6-luna",
        description=(
            "Default OpenAI chat model for Ask, ingestion metadata extraction, "
            "retrieval query rewriting and workspace CONTEXT.md synthesis. An "
            "organization's saved LLM settings override it for Ask."
        ),
    )
    capture_vision_model: str = Field(
        "gpt-4o-mini",
        description="Vision-capable model used to summarize approved captures.",
    )
    token_encryption_key: str = Field(
        default="",
        description=(
            "Fernet key (url-safe base64 32-byte) for encrypting OAuth tokens at rest. "
            "Required in production. Generate with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ),
    )

    google_client_id: str = Field(
        default="",
        description=(
            "Google OAuth / GIS client ID. Used as the audience when verifying "
            "Google ID tokens for sign-in, and for Workspace OAuth."
        ),
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth client secret.",
    )
    google_workspace_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/google/workspace/callback",
        description="OAuth redirect URI for Gmail/Drive Workspace sync consent.",
    )
    google_pubsub_topic: str = Field(
        default="",
        description=(
            "Cloud Pub/Sub topic used for Gmail/Drive push notifications, e.g. "
            "projects/my-project/topics/loom-google-workspace."
        ),
    )
    google_drive_webhook_url: str = Field(
        default="",
        description=(
            "Public HTTPS callback URL used for Drive changes.watch. Gmail push "
            "uses Pub/Sub and does not call this URL directly."
        ),
    )
    google_drive_webhook_secret: str = Field(
        default="",
        description="Random secret copied into Drive channel notifications and validated on receipt.",
    )
    zoom_client_id: str = Field(default="", description="Zoom OAuth app client ID.")
    zoom_client_secret: str = Field(default="", description="Zoom OAuth app client secret.")
    zoom_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/zoom/callback",
        description="OAuth redirect URI registered for the Zoom app.",
    )
    zoom_webhook_secret_token: str = Field(
        default="", description="Zoom event-subscription secret token."
    )
    frontend_url: str = Field(
        default="http://localhost:5173",
        description="Frontend origin used for OAuth success/error redirects.",
    )
    public_api_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Public base URL of this API, used in the Slack app manifest so Slack "
            "can deliver events. Locally this must be an HTTPS tunnel, not localhost."
        ),
    )
    microsoft_client_id: str = Field(
        default="",
        description="Microsoft Entra app client ID for Teams sync.",
    )
    microsoft_client_secret: str = Field(
        default="",
        description="Microsoft Entra app client secret for Teams sync.",
    )
    microsoft_tenant_id: str = Field(
        default="common",
        description="Microsoft tenant id, or 'common' for multi-tenant delegated OAuth.",
    )
    microsoft_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/microsoft/teams/callback",
        description="OAuth redirect URI registered for Microsoft Teams sync.",
    )
    microsoft_graph_webhook_url: str = Field(
        default="",
        description="Public HTTPS callback URL for Microsoft Graph Teams subscriptions.",
    )
    microsoft_graph_client_state: str = Field(
        default="",
        description="Shared secret used to validate Microsoft Graph subscription callbacks.",
    )
    github_token: str = Field(
        default="",
        description=(
            "Deprecated. GitHub access is now a per-user connector in Apps. "
            "This server-wide token is ignored."
        ),
    )
    google_pubsub_push_audience: str = Field(
        default="",
        description=(
            "Expected OIDC audience for Gmail Pub/Sub push requests "
            "(typically the full HTTPS push URL). Required in production."
        ),
    )
    cors_allowed_origins: str = Field(
        default="",
        description=(
            "Comma-separated extra browser origins allowed by CORS, in addition to FRONTEND_URL."
        ),
    )
    rate_limit_auth: str = Field(
        default="20/minute",
        description="slowapi limit string for POST /auth/google/signin and POST /orgs.",
    )
    rate_limit_query: str = Field(
        default="30/minute",
        description="slowapi limit string for POST /query.",
    )
    rate_limit_captures: str = Field(
        default="30/minute",
        description="slowapi limit string for capture create/analyze routes.",
    )
    rate_limit_ingest: str = Field(
        default="20/minute",
        description="slowapi limit string for ingest and /files/extract routes.",
    )

    @property
    def github_enabled(self) -> bool:
        """Deprecated: GitHub is enabled per user via the Apps connector."""

        return False

    @property
    def google_oauth_enabled(self) -> bool:
        """True when Google OAuth credentials are configured."""

        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def microsoft_oauth_enabled(self) -> bool:
        """True when Microsoft OAuth credentials are configured."""

        return bool(self.microsoft_client_id.strip() and self.microsoft_client_secret.strip())

    @property
    def zoom_oauth_enabled(self) -> bool:
        return bool(self.zoom_client_id.strip() and self.zoom_client_secret.strip())

    @property
    def resolved_session_secret(self) -> str:
        """Return the JWT signing secret, failing closed in production if unset."""

        secret = self.session_secret.strip()
        if secret:
            return secret
        if self.app_env == "development":
            return "dev-insecure-session-secret-change-me"
        raise ValueError("SESSION_SECRET is required when APP_ENV=production.")

    @property
    def dev_integrations_allowed(self) -> bool:
        """True only in development when ALLOW_DEV_INTEGRATIONS is enabled."""

        return self.app_env == "development" and self.allow_dev_integrations

    @property
    def resolved_cors_origins(self) -> list[str]:
        """Browser origins allowed by CORS (never ``*``)."""

        origins: list[str] = []
        primary = self.frontend_url.strip().rstrip("/")
        if primary:
            origins.append(primary)
        for part in self.cors_allowed_origins.split(","):
            cleaned = part.strip().rstrip("/")
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
        if not origins:
            origins.append("http://localhost:5173")
        return origins


_WEAK_SESSION_SECRETS = frozenset(
    {
        "",
        "dev-insecure-session-secret-change-me",
        "replace-with-a-long-random-value",
    }
)
_WEAK_NEO4J_PASSWORDS = frozenset({"please-change-me", "password", "neo4j"})
_WEAK_MS_CLIENT_STATES = frozenset({"", "dev-client-state"})


def validate_production_secrets(settings: Settings | None = None) -> None:
    """Refuse to start in production with empty or known-weak secrets."""

    cfg = settings or get_settings()
    if cfg.app_env != "production":
        return

    errors: list[str] = []
    session = cfg.session_secret.strip()
    if session in _WEAK_SESSION_SECRETS:
        errors.append("SESSION_SECRET must be set to a strong unique value in production.")
    if cfg.neo4j_password.strip() in _WEAK_NEO4J_PASSWORDS:
        errors.append("NEO4J_PASSWORD must not use a known weak default in production.")
    if not cfg.google_drive_webhook_secret.strip():
        errors.append("GOOGLE_DRIVE_WEBHOOK_SECRET is required in production.")
    if cfg.microsoft_graph_client_state.strip() in _WEAK_MS_CLIENT_STATES:
        errors.append(
            "MICROSOFT_GRAPH_CLIENT_STATE must be set to a non-default secret in production."
        )
    if not cfg.zoom_webhook_secret_token.strip():
        errors.append("ZOOM_WEBHOOK_SECRET_TOKEN is required in production.")
    if not cfg.google_pubsub_push_audience.strip():
        errors.append(
            "GOOGLE_PUBSUB_PUSH_AUDIENCE is required in production "
            "(full HTTPS Pub/Sub push URL)."
        )
    if not cfg.token_encryption_key.strip():
        errors.append("TOKEN_ENCRYPTION_KEY is required in production.")
    if cfg.blob_storage_backend == "s3" and not cfg.s3_bucket.strip():
        errors.append("S3_BUCKET is required when BLOB_STORAGE_BACKEND=s3.")
    if errors:
        raise RuntimeError("Production secret validation failed:\n- " + "\n- ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""

    return Settings()  # type: ignore[call-arg]

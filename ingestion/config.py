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
    postgres_url: str = Field(..., description="SQLAlchemy connection URL for the PostgreSQL/pgvector database.")
    neo4j_uri: str = Field(..., description="Bolt URI of the Neo4j instance, e.g. bolt://localhost:7687.")
    neo4j_username: str = Field(..., description="Username for authenticating against Neo4j.")
    neo4j_password: str = Field(..., description="Password for authenticating against Neo4j.")

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

    blob_storage_backend: Literal["local"] = Field(
        "local",
        description=(
            "Blob storage backend for raw uploaded files. Only 'local' is "
            "implemented today; the interface allows adding 's3'/'gcs' later."
        ),
    )
    blob_storage_root: str = Field(
        "./data/blobs",
        description="Filesystem root for the local blob storage backend.",
    )

    google_client_id: str = Field(
        default="",
        description="Google OAuth client ID for Calendar and other workspace apps.",
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth client secret.",
    )
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/google/calendar/callback",
        description="OAuth redirect URI registered in Google Cloud Console.",
    )
    google_workspace_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/google/workspace/callback",
        description="OAuth redirect URI for Gmail/Drive Workspace sync consent.",
    )
    google_pubsub_topic: str = Field(
        default="",
        description=(
            "Cloud Pub/Sub topic used for Gmail/Drive push notifications, e.g. "
            "projects/my-project/topics/companybrain-google-workspace."
        ),
    )
    google_drive_webhook_url: str = Field(
        default="",
        description=(
            "Public HTTPS callback URL used for Drive changes.watch. Gmail push "
            "uses Pub/Sub and does not call this URL directly."
        ),
    )
    frontend_url: str = Field(
        default="http://localhost:5173",
        description="Frontend origin used for OAuth success/error redirects.",
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
        default="dev-client-state",
        description="Shared secret used to validate Microsoft Graph subscription callbacks.",
    )

    @property
    def google_oauth_enabled(self) -> bool:
        """True when Google OAuth credentials are configured."""

        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def microsoft_oauth_enabled(self) -> bool:
        """True when Microsoft OAuth credentials are configured."""

        return bool(self.microsoft_client_id.strip() and self.microsoft_client_secret.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""

    return Settings()  # type: ignore[call-arg]

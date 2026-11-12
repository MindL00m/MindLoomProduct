"""Admin-managed organization AI provider settings (the AI connection)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from keeper.auth import require_admin_context, require_user_context
from core.anthropic_adapter import check_anthropic_key
from core.config import get_settings
from core.database import get_session_factory
from core.llm_provider import (
    ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    LLMConfiguration,
    LLMProviderError,
    check_local_endpoint,
    check_openai_key,
    get_llm_configuration,
)
from core.token_crypto import encrypt_token

router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])


class LLMSettingsPayload(BaseModel):
    provider: Literal["openai", "anthropic", "local"]
    base_url: str | None = None
    model: str = DEFAULT_OPENAI_MODEL
    context_window: int = Field(default=16384, ge=1024, le=1_000_000)
    supports_tools: bool = True
    # Write-only. Omitted or null keeps the saved key; "" removes it.
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/") if value else None


def _public(config: LLMConfiguration) -> dict:
    """Settings as shown to users: whether a key is saved, never the key."""

    return {
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "context_window": config.context_window,
        "supports_tools": config.supports_tools,
        "has_api_key": bool(config.api_key),
    }


@router.get("")
async def read_settings(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    return _public(await get_llm_configuration(ctx[0]))


@router.get("/health")
async def provider_health(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    config = await get_llm_configuration(ctx[0])
    try:
        if config.provider == "anthropic":
            key = config.api_key or get_settings().anthropic_api_key
            if not key:
                raise LLMProviderError("Claude is selected but no API key is saved.")
            await check_anthropic_key(key, config.model)
        elif config.provider == "local":
            await check_local_endpoint(config.base_url or "", config.api_key)
        elif config.api_key:
            await check_openai_key(config.api_key, config.model)
        else:
            return {"status": "configured", "provider": "openai"}
    except LLMProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "provider": config.provider, "model": config.model}


@router.put("")
async def update_settings(
    payload: LLMSettingsPayload,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict:
    current = await get_llm_configuration(ctx[0])
    model = payload.model.strip()
    new_key = (payload.api_key or "").strip()
    # A saved key belongs to the provider (and self-hosted address) it was saved for.
    same_target = current.provider == payload.provider and (
        payload.provider != "local" or current.base_url == payload.base_url
    )
    kept_key = current.api_key if payload.api_key is None and same_target else None
    api_key = new_key or kept_key
    context_window = payload.context_window
    try:
        if payload.provider == "local":
            if not payload.base_url or not model:
                raise HTTPException(status_code=422, detail="Local base URL and model are required.")
            await check_local_endpoint(payload.base_url, api_key)
        elif payload.provider == "anthropic":
            model = model or DEFAULT_ANTHROPIC_MODEL
            context_window = max(context_window, ANTHROPIC_CONTEXT_WINDOW)
            usable = api_key or get_settings().anthropic_api_key
            if not usable:
                raise HTTPException(status_code=422, detail="A Claude API key is required.")
            await check_anthropic_key(usable, model)
        elif api_key:
            await check_openai_key(api_key, model or DEFAULT_OPENAI_MODEL)
    except LLMProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = LLMConfiguration(
        provider=payload.provider,
        base_url=payload.base_url if payload.provider == "local" else None,
        model=model or DEFAULT_OPENAI_MODEL,
        context_window=context_window,
        supports_tools=payload.supports_tools,
        api_key=api_key,
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(text("""
            INSERT INTO organization_llm_settings
                (org_id, provider, base_url, model, context_window, supports_tools, api_key, updated_at)
            VALUES (:org_id, :provider, :base_url, :model, :context_window, :supports_tools, :api_key, now())
            ON CONFLICT (org_id) DO UPDATE SET provider=excluded.provider,
                base_url=excluded.base_url, model=excluded.model,
                context_window=excluded.context_window,
                supports_tools=excluded.supports_tools, api_key=excluded.api_key, updated_at=now()
        """), {
            "org_id": ctx[0], "provider": saved.provider, "base_url": saved.base_url,
            "model": saved.model, "context_window": saved.context_window,
            "supports_tools": saved.supports_tools, "api_key": encrypt_token(saved.api_key),
        })
    return _public(saved)

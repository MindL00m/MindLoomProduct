"""Admin-managed organization LLM provider settings."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from keeper.auth import require_admin_context, require_user_context
from core.database import get_session_factory
from core.llm_provider import LLMProviderError, check_local_endpoint, get_llm_configuration

router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])


class LLMSettingsPayload(BaseModel):
    provider: Literal["openai", "local"]
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    context_window: int = Field(default=16384, ge=1024, le=1_000_000)
    supports_tools: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/") if value else None


@router.get("")
async def read_settings(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    config = await get_llm_configuration(ctx[0])
    return config.__dict__


@router.get("/health")
async def provider_health(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    config = await get_llm_configuration(ctx[0])
    if config.provider == "openai":
        return {"status": "configured", "provider": "openai"}
    try:
        await check_local_endpoint(config.base_url or "")
    except LLMProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "provider": "local", "model": config.model}


@router.put("")
async def update_settings(
    payload: LLMSettingsPayload,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict:
    if payload.provider == "local":
        if not payload.base_url or not payload.model.strip():
            raise HTTPException(status_code=422, detail="Local base URL and model are required.")
        try:
            await check_local_endpoint(payload.base_url)
        except LLMProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(text("""
            INSERT INTO organization_llm_settings
                (org_id, provider, base_url, model, context_window, supports_tools, updated_at)
            VALUES (:org_id, :provider, :base_url, :model, :context_window, :supports_tools, now())
            ON CONFLICT (org_id) DO UPDATE SET provider=excluded.provider,
                base_url=excluded.base_url, model=excluded.model,
                context_window=excluded.context_window,
                supports_tools=excluded.supports_tools, updated_at=now()
        """), {"org_id": ctx[0], **payload.model_dump()})
    return payload.model_dump()

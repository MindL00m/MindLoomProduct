"""SAP connection: connect with a technical user, status and disconnect."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.rate_limit import actions_limit, rate_limit
from weaver import sap
from weaver.integrations import require_user_context

router = APIRouter(tags=["sap"])

Context = Annotated[tuple[str, str], Depends(require_user_context)]


class SapConnectInput(BaseModel):
    base_url: str = Field(min_length=4, max_length=300)
    system: Literal["s4hana", "business_one"] = "s4hana"
    auth: Literal["basic", "oauth", "certificate"] = "basic"
    via: Literal["direct", "relay"] = "direct"
    username: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)
    test_service: str | None = Field(default=None, max_length=300)
    sap_client: str | None = Field(default=None, max_length=3)
    token_url: str | None = Field(default=None, max_length=400)
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)
    certificate_pem: str | None = Field(default=None, max_length=20_000)
    private_key_pem: str | None = Field(default=None, max_length=20_000)
    company_db: str | None = Field(default=None, max_length=80)
    relay_code: str | None = Field(default=None, max_length=20)


@router.get("/integrations/sap")
async def sap_status(ctx: Context) -> dict:
    """Connection status. Never the password."""

    return await sap.get_status(*ctx)


@router.post("/integrations/sap/connect", dependencies=[rate_limit(actions_limit, "actions")])
async def sap_connect(body: SapConnectInput, ctx: Context) -> dict:
    """Check the address and sign-in with one real call, then save."""

    return await sap.connect(*ctx, **body.model_dump())


@router.delete("/integrations/sap")
async def sap_disconnect(ctx: Context) -> dict:
    return await sap.disconnect(*ctx)

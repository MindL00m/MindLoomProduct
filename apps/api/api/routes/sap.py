"""SAP connection: connect with a technical user, status and disconnect."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.rate_limit import actions_limit, rate_limit
from weaver import sap
from weaver.integrations import require_user_context

router = APIRouter(tags=["sap"])

Context = Annotated[tuple[str, str], Depends(require_user_context)]


class SapConnectInput(BaseModel):
    base_url: str = Field(min_length=4, max_length=300)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    test_service: str = Field(min_length=3, max_length=300)
    sap_client: str | None = Field(default=None, max_length=3)


@router.get("/integrations/sap")
async def sap_status(ctx: Context) -> dict:
    """Connection status. Never the password."""

    return await sap.get_status(*ctx)


@router.post("/integrations/sap/connect", dependencies=[rate_limit(actions_limit, "actions")])
async def sap_connect(body: SapConnectInput, ctx: Context) -> dict:
    """Check the address and user against one allowed service, then save."""

    return await sap.connect(*ctx, base_url=body.base_url, username=body.username, password=body.password,
                             test_service=body.test_service, sap_client=body.sap_client)


@router.delete("/integrations/sap")
async def sap_disconnect(ctx: Context) -> dict:
    return await sap.disconnect(*ctx)

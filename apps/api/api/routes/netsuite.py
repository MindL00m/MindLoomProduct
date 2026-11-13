"""NetSuite connection: the two setup steps, status and disconnect."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.rate_limit import actions_limit, rate_limit
from weaver import netsuite
from weaver.integrations import require_user_context

router = APIRouter(tags=["netsuite"])

Context = Annotated[tuple[str, str], Depends(require_user_context)]


class NetSuiteSetupInput(BaseModel):
    account_id: str = Field(min_length=3, max_length=41)


class NetSuiteConnectInput(BaseModel):
    client_id: str = Field(min_length=20, max_length=128)
    certificate_id: str = Field(min_length=8, max_length=128)


@router.get("/integrations/netsuite")
async def netsuite_status(ctx: Context) -> dict:
    """Connection status and the public certificate. Never the private key or a token."""

    return await netsuite.get_status(*ctx)


@router.post("/integrations/netsuite/setup", dependencies=[rate_limit(actions_limit, "actions")])
async def netsuite_setup(body: NetSuiteSetupInput, ctx: Context) -> dict:
    """Step 1: make the certificate to upload in NetSuite. Replaces an earlier one."""

    return await netsuite.start_setup(*ctx, body.account_id)


@router.post("/integrations/netsuite/connect", dependencies=[rate_limit(actions_limit, "actions")])
async def netsuite_connect(body: NetSuiteConnectInput, ctx: Context) -> dict:
    """Step 2: check the client ID and certificate ID by signing in to NetSuite."""

    return await netsuite.finish_setup(*ctx, body.client_id, body.certificate_id)


@router.delete("/integrations/netsuite")
async def netsuite_disconnect(ctx: Context) -> dict:
    return await netsuite.disconnect(*ctx)

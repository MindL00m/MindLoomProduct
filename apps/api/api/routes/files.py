"""LoomBot Files tab: the signed-in user's private CSV files."""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from keeper.auth import require_user_context
from loombot import files as files_service

router = APIRouter(prefix="/loombot/files", tags=["loombot-files"])


class CreateFileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = ""


class UpdateFileInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = None


@router.get("")
async def list_files(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    return await files_service.list_files(*ctx)


@router.post("", status_code=201)
async def create_file(
    body: CreateFileInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.create_file(*ctx, body.name, body.content)


@router.get("/{file_id}")
async def get_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.get_file(*ctx, file_id)


@router.put("/{file_id}")
async def update_file(
    file_id: str,
    body: UpdateFileInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.update_file(
        *ctx, file_id, content=body.content, name=body.name
    )


@router.post("/{file_id}/undo")
async def undo_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.undo_file(*ctx, file_id)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> Response:
    await files_service.delete_file(*ctx, file_id)
    return Response(status_code=204)

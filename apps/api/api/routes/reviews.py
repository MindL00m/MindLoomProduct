"""Administrator knowledge review and expert proposal endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from integrations import require_admin_context, require_user_context
from review_workflows import (
    answer_expert_request,
    create_review,
    expert_notification_count,
    expert_message_notification_count,
    get_expert_thread_messages,
    list_expert_threads,
    list_message_contacts,
    list_expert_requests,
    list_reviews,
    moderate_expert_answer,
    resolve_review,
    send_expert_message,
    send_proposed_expert_message,
    start_expert_conversation,
    upsert_review_schedule,
)

router = APIRouter(prefix="/knowledge/reviews", tags=["knowledge reviews"])


class ProposalInput(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    owner_user_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class DecisionInput(BaseModel):
    status: Literal["approved", "rejected", "resolved"]
    note: str | None = None


class ReviewScheduleInput(BaseModel):
    source_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    interval_days: int = Field(default=180, ge=1, le=3650)
    next_review_at: datetime
    expires_at: datetime | None = None


class ExpertAnswerInput(BaseModel):
    answer: str = Field(min_length=1)


class ModerationInput(BaseModel):
    action: Literal["edit", "remove"]
    answer: str | None = None


class StartConversationInput(BaseModel):
    expert_user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SendMessageInput(BaseModel):
    message: str = Field(min_length=1)


class SendProposedMessageInput(BaseModel):
    recipient_user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    client_request_id: str | None = None


@router.get("")
async def reviews(
    review_type: str | None = None,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> list[dict]:
    return await list_reviews(ctx[0], review_type=review_type)


@router.get("/expert-inbox")
async def expert_inbox(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_expert_requests(*ctx)


@router.get("/expert-inbox/count")
async def expert_inbox_count(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict[str, int]:
    return {"count": await expert_message_notification_count(*ctx)}


@router.get("/messages")
async def message_threads(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_expert_threads(*ctx)


@router.get("/messages/contacts")
async def message_contacts(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_message_contacts(*ctx)


@router.post("/messages")
async def start_message_thread(
    request: StartConversationInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict[str, str]:
    review_id = await start_expert_conversation(
        org_id=ctx[0],
        requester_user_id=ctx[1],
        expert_user_id=request.expert_user_id,
        message=request.message,
    )
    return {"review_id": review_id, "status": "open"}


@router.post("/messages/send-proposed")
async def send_proposed_message(
    request: SendProposedMessageInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict[str, str]:
    return await send_proposed_expert_message(
        org_id=ctx[0],
        requester_user_id=ctx[1],
        recipient_user_id=request.recipient_user_id,
        message=request.message,
    )


@router.get("/messages/{review_id}")
async def thread_messages(
    review_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await get_expert_thread_messages(*ctx, review_id)


@router.post("/messages/{review_id}")
async def send_message(
    review_id: str,
    request: SendMessageInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await send_expert_message(
        org_id=ctx[0], user_id=ctx[1], review_id=review_id, body=request.message
    )


@router.post("/expert-inbox/{review_id}/answer")
async def answer_request(
    review_id: str,
    request: ExpertAnswerInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await answer_expert_request(*ctx, review_id, request.answer)


@router.post("/expert-inbox/{review_id}/answer-media")
async def answer_request_media(
    review_id: str,
    answer: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    parts = [answer.strip()] if answer.strip() else []
    if file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Attached response is empty.")
        from openai import AsyncOpenAI
        from config import get_settings

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_request_timeout_seconds,
        )
        content_type = file.content_type or "application/octet-stream"
        if content_type.startswith("image/"):
            import base64
            response = await client.chat.completions.create(
                model=settings.capture_vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the expert's reusable instructions and decision guidance from this screenshot."},
                        {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{base64.b64encode(data).decode()}"}},
                    ],
                }],
                max_tokens=700,
            )
            parts.append(response.choices[0].message.content or "")
        elif content_type.startswith(("audio/", "video/")):
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=(file.filename or "expert-recording", data, content_type),
            )
            parts.append(transcript.text)
        elif content_type.startswith("text/"):
            parts.append(data.decode("utf-8", errors="replace"))
        else:
            raise HTTPException(
                status_code=400,
                detail="Attach text, an image, audio, or a short video recording.",
            )
    combined = "\n\n".join(part for part in parts if part.strip())
    if not combined:
        raise HTTPException(status_code=400, detail="Provide an answer or attachment.")
    return await answer_expert_request(*ctx, review_id, combined)


@router.post("/expert-answers/{review_id}/moderate")
async def moderate_answer(
    review_id: str,
    request: ModerationInput,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict:
    return await moderate_expert_answer(
        org_id=ctx[0], admin_user_id=ctx[1], review_id=review_id,
        action=request.action, answer=request.answer,
    )


@router.post("/proposals")
async def propose(
    request: ProposalInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict[str, str]:
    org_id, user_id = ctx
    review_id = await create_review(
        org_id=org_id, review_type="proposal",
        title=f"Expert answer: {request.question}",
        description="Legacy proposal awaiting migration to an assigned expert request.",
        created_by=user_id, owner_user_id=request.owner_user_id,
        source_ids=request.source_ids, proposed_content=request.answer,
    )
    return {"review_id": review_id, "status": "open"}


@router.post("/verification")
async def request_verification(
    request: ProposalInput,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict[str, str]:
    org_id, user_id = ctx
    review_id = await create_review(
        org_id=org_id, review_type="verification",
        title=request.question, description=request.answer, created_by=user_id,
        owner_user_id=request.owner_user_id, source_ids=request.source_ids,
    )
    return {"review_id": review_id, "status": "open"}


@router.post("/schedules")
async def save_schedule(
    request: ReviewScheduleInput,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict[str, str]:
    schedule_id = await upsert_review_schedule(
        org_id=ctx[0], source_id=request.source_id,
        owner_user_id=request.owner_user_id, interval_days=request.interval_days,
        next_review_at=request.next_review_at, expires_at=request.expires_at,
    )
    return {"schedule_id": schedule_id, "status": "active"}


@router.post("/{review_id}/decision")
async def decide(
    review_id: str,
    request: DecisionInput,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict:
    return await resolve_review(
        ctx[0], review_id, status=request.status, actor_user_id=ctx[1], note=request.note
    )

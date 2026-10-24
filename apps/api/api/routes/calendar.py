"""Google Calendar scheduling for meetings drafted in Ask and workspaces."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from keeper.auth import require_user_context
from shuttle import calendar_meetings

router = APIRouter(prefix="/calendar", tags=["calendar"])


class AvailabilityInput(BaseModel):
    attendee_emails: list[str] = Field(default_factory=list, max_length=50)
    duration_minutes: int = Field(default=30, ge=5, le=720)
    start_date: str | None = None
    end_date: str | None = None
    earliest_time: str | None = None
    latest_time: str | None = None
    include_weekends: bool = False
    timezone: str | None = None


class CreateMeetingInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=8000)
    location: str = Field(default="", max_length=500)
    add_video_call: bool = True
    timezone: str | None = None


@router.get("/status")
async def status(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    """Whether the signed-in user can schedule meetings on Google Calendar."""

    return await calendar_meetings.calendar_status(*ctx)


@router.post("/availability")
async def availability(
    body: AvailabilityInput, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    """Free times shared by the user and the attendees whose calendars are readable."""

    return await calendar_meetings.find_free_times(
        *ctx,
        attendee_emails=body.attendee_emails,
        duration_minutes=body.duration_minutes,
        start_date=body.start_date,
        end_date=body.end_date,
        earliest_time=body.earliest_time,
        latest_time=body.latest_time,
        include_weekends=body.include_weekends,
        user_timezone=body.timezone,
    )


@router.post("/meetings", status_code=201)
async def create_meeting(
    body: CreateMeetingInput, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    """Create an approved meeting on the user's primary calendar and send invitations."""

    return await calendar_meetings.create_meeting(
        *ctx,
        title=body.title,
        start=body.start,
        end=body.end,
        attendees=body.attendees,
        description=body.description,
        location=body.location,
        add_video_call=body.add_video_call,
        user_timezone=body.timezone,
    )

"""Persistent webhook subscriptions, separate from incremental sync cursors."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base
from database import get_session_factory


class IntegrationSubscriptionRow(Base):
    __tablename__ = "integration_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "provider",
            "resource",
            name="uq_integration_subscription_resource",
        ),
    )

    subscription_key: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expiration: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


async def upsert_subscription(
    *,
    org_id: str,
    user_id: str,
    provider: str,
    external_id: str,
    resource: str,
    resource_id: str | None = None,
    expiration: datetime | None = None,
    status: str = "active",
) -> IntegrationSubscriptionRow:
    now = datetime.now(timezone.utc)
    values = {
        "subscription_key": str(uuid4()),
        "org_id": org_id,
        "user_id": user_id,
        "provider": provider,
        "external_id": external_id,
        "resource": resource,
        "resource_id": resource_id,
        "expiration": expiration,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            stmt = pg_insert(IntegrationSubscriptionRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_integration_subscription_resource",
                set_={
                    "external_id": external_id,
                    "resource_id": resource_id,
                    "expiration": expiration,
                    "status": status,
                    "updated_at": now,
                },
            ).returning(IntegrationSubscriptionRow)
            result = await session.execute(stmt)
            return result.scalar_one()


async def find_subscription(
    provider: str, external_id: str
) -> IntegrationSubscriptionRow | None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(IntegrationSubscriptionRow).where(
                IntegrationSubscriptionRow.provider == provider,
                IntegrationSubscriptionRow.external_id == external_id,
                IntegrationSubscriptionRow.status == "active",
            )
        )
        return result.scalar_one_or_none()


async def expiring_subscriptions(
    provider: str, before: datetime
) -> list[IntegrationSubscriptionRow]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(IntegrationSubscriptionRow).where(
                IntegrationSubscriptionRow.provider == provider,
                IntegrationSubscriptionRow.status == "active",
                IntegrationSubscriptionRow.expiration.is_not(None),
                IntegrationSubscriptionRow.expiration <= before,
            )
        )
        return list(result.scalars())

"""Organization and user tenancy: Postgres persistence + FastAPI dependency."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Header, HTTPException
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from database import get_neo4j_driver, get_session_factory
from models import AuthSessionResponse, OrgSummaryResponse

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


class Base(DeclarativeBase):
    pass


class OrganizationRow(Base):
    __tablename__ = "organizations"

    org_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    google_sub: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def normalize_domain(raw: str) -> str:
    """Strip protocol/path and lower-case a domain string."""

    cleaned = raw.strip().lower()
    cleaned = cleaned.removeprefix("https://").removeprefix("http://")
    cleaned = cleaned.removeprefix("www.")
    cleaned = cleaned.split("/")[0].strip()
    return cleaned


def email_domain(email: str) -> str:
    """Extract and normalize the domain from an email address."""

    parts = email.strip().lower().split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid email address.")
    return normalize_domain(parts[1])


async def get_org_by_id(org_id: str) -> OrganizationRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await session.get(OrganizationRow, org_id)


async def get_org_by_domain(domain: str) -> OrganizationRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(OrganizationRow).where(OrganizationRow.domain == normalize_domain(domain))
        )
        return result.scalar_one_or_none()


async def upsert_user(
    *,
    org_id: str,
    email: str,
    name: str | None = None,
    photo_url: str | None = None,
    role: str = "member",
) -> UserRow:
    now = datetime.now(timezone.utc)
    normalized_email = email.strip().lower()
    user_id = str(uuid4())

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            stmt = pg_insert(UserRow).values(
                user_id=user_id,
                org_id=org_id,
                email=normalized_email,
                name=name,
                photo_url=photo_url,
                role=role,
                created_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserRow.email],
                set_={
                    "org_id": org_id,
                    "name": name,
                    "photo_url": photo_url,
                    "role": role,
                },
            )
            await session.execute(stmt)
        result = await session.execute(
            select(UserRow).where(UserRow.email == normalized_email)
        )
        user = result.scalar_one()
    return user


async def create_org(
    *,
    name: str,
    domain: str,
    admin_email: str,
    admin_name: str | None = None,
    admin_photo_url: str | None = None,
) -> AuthSessionResponse:
    domain_norm = normalize_domain(domain)
    if not _DOMAIN_RE.match(domain_norm):
        raise ValueError("Invalid domain format.")

    admin_domain = email_domain(admin_email)
    if admin_domain != domain_norm:
        raise ValueError("Admin email domain must match the organization domain.")

    existing = await get_org_by_domain(domain_norm)
    if existing is not None:
        raise ValueError(f"An organization already exists for domain {domain_norm}.")

    org_id = str(uuid4())
    now = datetime.now(timezone.utc)

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                OrganizationRow(
                    org_id=org_id,
                    name=name.strip(),
                    domain=domain_norm,
                    created_at=now,
                )
            )

    user = await upsert_user(
        org_id=org_id,
        email=admin_email,
        name=admin_name,
        photo_url=admin_photo_url,
        role="admin",
    )

    logger.info("Created organization %s (%s)", org_id, domain_norm)
    return AuthSessionResponse(
        org_id=org_id,
        org_name=name.strip(),
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        photo_url=user.photo_url,
    )


async def google_signin(
    *,
    email: str,
    name: str | None = None,
    photo_url: str | None = None,
) -> AuthSessionResponse:
    domain = email_domain(email)
    org = await get_org_by_domain(domain)
    if org is None:
        raise HTTPException(
            status_code=404,
            detail=f"No organization found for domain {domain}. Set up a new organization first.",
        )

    user = await upsert_user(
        org_id=org.org_id,
        email=email,
        name=name,
        photo_url=photo_url,
    )

    return AuthSessionResponse(
        org_id=org.org_id,
        org_name=org.name,
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        photo_url=user.photo_url,
    )


async def get_org_summary(org_id: str) -> OrgSummaryResponse:
    org = await get_org_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    cypher = """
    MATCH (p:Person {org_id: $org_id})
    WHERE p.canonical_email IS NOT NULL
    RETURN count(p) AS people,
           count(DISTINCT p.department) AS departments
    """
    groups_cypher = """
    MATCH (p:Person {org_id: $org_id})
    WHERE p.canonical_email IS NOT NULL AND size(coalesce(p.groups, [])) > 0
    UNWIND p.groups AS g
    RETURN count(DISTINCT g) AS groups
    """

    async def _read(tx):  # type: ignore[no-untyped-def]
        people_result = await tx.run(
            """
            MATCH (p:Person {org_id: $org_id})
            WHERE p.canonical_email IS NOT NULL
            RETURN count(p) AS people,
                   count(DISTINCT p.department) AS departments
            """,
            org_id=org_id,
        )
        people_record = await people_result.single()
        groups_result = await tx.run(groups_cypher, org_id=org_id)
        groups_record = await groups_result.single()
        return people_record, groups_record

    driver = get_neo4j_driver()
    async with driver.session() as session:
        people_record, groups_record = await session.execute_read(_read)

    people = people_record["people"] if people_record else 0
    departments = people_record["departments"] if people_record else 0
    groups = groups_record["groups"] if groups_record else 0

    return OrgSummaryResponse(
        organization=org.name,
        people=people,
        departments=departments,
        groups=groups,
    )


async def require_org_id(x_org_id: str = Header(..., alias="X-Org-Id")) -> str:
    """FastAPI dependency: validate ``X-Org-Id`` and return the org id."""

    if not x_org_id.strip():
        raise HTTPException(status_code=401, detail="Missing organization context.")
    org = await get_org_by_id(x_org_id.strip())
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid or unknown organization.")
    return x_org_id.strip()

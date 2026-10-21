"""Organization and user tenancy: Postgres persistence + JWT FastAPI dependencies."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from sqlalchemy import DateTime, String, Text, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.database import get_neo4j_driver, get_session_factory
from core.models import AuthSessionResponse, OrgSummaryResponse
from keeper.session_tokens import (
    GoogleIdentity,
    decode_access_token,
    issue_access_token,
    verify_google_id_token,
)

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


def _session_response(
    *,
    org_id: str,
    org_name: str,
    user: UserRow,
) -> AuthSessionResponse:
    return AuthSessionResponse(
        org_id=org_id,
        org_name=org_name,
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        photo_url=user.photo_url,
        role=user.role,  # type: ignore[arg-type]
        access_token=issue_access_token(
            org_id=org_id,
            user_id=user.user_id,
            role=user.role,
            email=user.email,
        ),
    )


async def get_org_by_id(org_id: str) -> OrganizationRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await session.get(OrganizationRow, org_id)


async def get_user_by_id(org_id: str, user_id: str) -> UserRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(UserRow).where(UserRow.org_id == org_id, UserRow.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_user_access_tokens(org_id: str, user_id: str) -> list[str]:
    """Return stable identities used by chunk-level search permissions."""

    user = await get_user_by_id(org_id, user_id)
    if user is None:
        return []
    email = user.email.lower()
    domain = email.rsplit("@", 1)[1] if "@" in email else ""
    tokens = [f"org:{org_id}", f"user:{user.user_id}", email]
    if domain:
        tokens.append(f"domain:{domain}")
    try:
        driver = get_neo4j_driver()
        async with driver.session() as graph_session:
            result = await graph_session.run(
                """
                MATCH (p:Person {org_id: $org_id})
                WHERE toLower(p.canonical_email) = $email OR toLower(p.email) = $email
                RETURN p.department AS department
                LIMIT 1
                """,
                org_id=org_id,
                email=user.email.lower(),
            )
            record = await result.single()
            department = record.get("department") if record else None
            if department:
                tokens.append(f"department:{str(department).lower()}")
    except Exception:  # noqa: BLE001 - identity still works without graph enrichment
        logger.warning("Could not resolve department access token for user %s", user_id)
    # Mail synced from the user's own connected accounts is visible to that
    # account's address, which may differ from their MindLoom login email.
    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = await session.execute(
                text(
                    "SELECT DISTINCT lower(account_email) FROM app_connections "
                    "WHERE org_id=:org AND user_id=:user AND account_email IS NOT NULL"
                ),
                {"org": org_id, "user": user_id},
            )
            for (account_email,) in rows:
                if account_email and "@" in account_email and account_email not in tokens:
                    tokens.append(account_email)
    except Exception:  # noqa: BLE001 - identity still works without connection lookup
        logger.warning("Could not resolve connected account tokens for user %s", user_id)
    return tokens


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
    google_sub: str | None = None,
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
            values: dict = {
                "user_id": user_id,
                "org_id": org_id,
                "email": normalized_email,
                "name": name,
                "photo_url": photo_url,
                "role": role,
                "created_at": now,
            }
            update_set: dict = {
                "org_id": org_id,
                "name": name,
                "photo_url": photo_url,
            }
            if google_sub is not None:
                values["google_sub"] = google_sub
                update_set["google_sub"] = google_sub
            stmt = pg_insert(UserRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserRow.email],
                set_=update_set,
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
    identity: GoogleIdentity,
) -> AuthSessionResponse:
    domain_norm = normalize_domain(domain)
    if not _DOMAIN_RE.match(domain_norm):
        raise ValueError("Invalid domain format.")

    admin_domain = email_domain(identity.email)
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
        email=identity.email,
        google_sub=identity.sub,
        name=identity.name,
        photo_url=identity.picture,
        role="admin",
    )

    logger.info("Created organization %s (%s)", org_id, domain_norm)
    return _session_response(org_id=org_id, org_name=name.strip(), user=user)


async def google_signin(*, identity: GoogleIdentity) -> AuthSessionResponse:
    domain = email_domain(identity.email)
    org = await get_org_by_domain(domain)
    if org is None:
        raise HTTPException(
            status_code=404,
            detail=f"No organization found for domain {domain}. Set up a new organization first.",
        )

    user = await upsert_user(
        org_id=org.org_id,
        email=identity.email,
        google_sub=identity.sub,
        name=identity.name,
        photo_url=identity.picture,
    )

    return _session_response(org_id=org.org_id, org_name=org.name, user=user)


async def get_org_summary(org_id: str) -> OrgSummaryResponse:
    org = await get_org_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

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


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing access token.")
    return token.strip()


async def require_user_context(request: Request) -> tuple[str, str]:
    """Validate Bearer JWT and ensure the user still belongs to the org."""

    claims = decode_access_token(_bearer_token(request))
    user = await get_user_by_id(claims.org_id, claims.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid user for this organization.")
    org = await get_org_by_id(claims.org_id)
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid or unknown organization.")
    return claims.org_id, claims.user_id


async def require_admin_context(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> tuple[str, str]:
    """Require an organization administrator for privileged APIs."""

    org_id, user_id = ctx
    user = await get_user_by_id(org_id, user_id)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="Organization admin access is required.")
    return ctx


async def require_org_id(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> str:
    """Return the authenticated organization id from the Bearer JWT."""

    org_id, _ = ctx
    return org_id


# Re-export for callers that verify Google tokens at the route layer.
__all__ = [
    "Base",
    "OrganizationRow",
    "UserRow",
    "normalize_domain",
    "email_domain",
    "get_org_by_id",
    "get_user_by_id",
    "get_user_access_tokens",
    "get_org_by_domain",
    "upsert_user",
    "create_org",
    "google_signin",
    "get_org_summary",
    "require_user_context",
    "require_admin_context",
    "require_org_id",
    "verify_google_id_token",
]

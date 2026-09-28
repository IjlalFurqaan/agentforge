"""Registration, login and refresh-token rotation with reuse detection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, Unauthorized
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import RefreshToken, Role, User

log = get_logger(__name__)


@dataclass
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user: User


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; everything we store is UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def register_user(
    session: AsyncSession, settings: Settings, *, email: str, password: str, full_name: str
) -> User:
    if not settings.allow_registration:
        raise Forbidden("Registration is disabled")
    email = email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise Conflict("An account with this email already exists")
    # The first account on a fresh installation becomes the administrator.
    user_count = await session.scalar(select(func.count()).select_from(User))
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role=Role.admin if user_count == 0 else Role.engineer,
    )
    session.add(user)
    await session.flush()
    log.info("user_registered", user_id=str(user.id), role=user.role)
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.lower()))
    # verify_password runs a full hash even when the user does not exist (timing equalisation).
    if not verify_password(password, user.password_hash if user else None) or user is None:
        raise Unauthorized("Invalid email or password")
    if not user.is_active:
        raise Unauthorized("Invalid email or password")
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    return user


async def issue_tokens(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[IssuedTokens, RefreshToken]:
    access, ttl = create_access_token(user.id, user.role.value, settings)
    raw_refresh = new_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    record = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(raw_refresh),
        expires_at=expires_at,
        user_agent=(user_agent or "")[:300] or None,
        ip_address=ip_address,
    )
    session.add(record)
    await session.flush()
    return IssuedTokens(access, ttl, raw_refresh, expires_at, user), record


async def rotate_refresh_token(
    session: AsyncSession,
    settings: Settings,
    raw_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """Exchange a refresh token for a new pair. Reusing a rotated token revokes the family."""
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if record is None:
        raise Unauthorized("Invalid refresh token")
    now = datetime.now(UTC)
    if record.revoked_at is not None:
        # Token reuse: someone replayed a token that was already rotated or revoked. Assume theft
        # and kill every token descended from the same login.
        await revoke_family(session, record.family_id)
        log.warning("refresh_token_reuse_detected", user_id=str(record.user_id),
                    family_id=str(record.family_id))
        raise Unauthorized("Refresh token reuse detected; please sign in again")
    if _aware(record.expires_at) <= now:
        raise Unauthorized("Refresh token expired")
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Account disabled")
    issued, new_record = await issue_tokens(
        session, settings, user, family_id=record.family_id, user_agent=user_agent, ip_address=ip_address
    )
    record.revoked_at = now
    record.replaced_by_id = new_record.id
    return issued


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def logout(session: AsyncSession, raw_token: str | None) -> None:
    if not raw_token:
        return
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if record is not None:
        await revoke_family(session, record.family_id)

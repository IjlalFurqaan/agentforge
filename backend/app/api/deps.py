"""Request dependencies: DB session, current user, RBAC."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.security import TokenError, decode_access_token
from app.db.models import ROLE_RANK, Role, User
from app.db.session import get_session

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token") from exc
    # Re-load the user on every request so deactivation and role changes apply immediately.
    user = await session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise Unauthorized("Account not found or disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: Role) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _dep(user: CurrentUser) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise Forbidden(f"Requires role '{minimum.value}' or higher")
        return user

    return _dep


EngineerUser = Annotated[User, Depends(require_role(Role.engineer))]
AdminUser = Annotated[User, Depends(require_role(Role.admin))]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

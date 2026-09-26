from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, SettingsDep, client_ip
from app.core.errors import NotFound, RateLimited, Unauthorized
from app.db.models import GitHubConnection, User
from app.schemas.auth import LoginRequest, RegisterRequest, RoleUpdate, TokenResponse, UserOut
from app.services import auth_service
from app.services.auth_service import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, settings: SettingsDep, issued: IssuedTokens) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=issued.refresh_token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=f"{settings.api_prefix}/auth",
        max_age=settings.refresh_token_ttl_days * 86400,
    )


async def user_out(session: SessionDep, user: User) -> UserOut:
    github = await session.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    out = UserOut.model_validate(user)
    out.github_login = github.login if github else None
    return out


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: SessionDep, settings: SettingsDep) -> UserOut:
    user = await auth_service.register_user(
        session, settings, email=body.email, password=body.password, full_name=body.full_name
    )
    return await user_out(session, user)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    limiter = request.app.state.rate_limiter
    key = f"login:{client_ip(request)}:{body.email.lower()}"
    if not await limiter.hit(key, settings.login_rate_limit, settings.login_rate_window_seconds):
        raise RateLimited("Too many login attempts; try again later")
    user = await auth_service.authenticate(session, email=body.email, password=body.password)
    await limiter.reset(key)
    issued, _ = await auth_service.issue_tokens(
        session, settings, user, user_agent=request.headers.get("user-agent"), ip_address=client_ip(request)
    )
    _set_refresh_cookie(response, settings, issued)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in,
                         user=await user_out(session, user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse | JSONResponse:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise Unauthorized("Missing refresh token")
    try:
        issued = await auth_service.rotate_refresh_token(
            session, settings, raw, user_agent=request.headers.get("user-agent"), ip_address=client_ip(request)
        )
    except Unauthorized as exc:
        # Persist the family revocation done during reuse detection, and clear the cookie.
        await session.commit()
        failure = JSONResponse(
            status_code=401, content={"error": {"code": exc.code, "message": exc.message, "details": {}}}
        )
        failure.delete_cookie(settings.refresh_cookie_name, path=f"{settings.api_prefix}/auth")
        return failure
    _set_refresh_cookie(response, settings, issued)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in,
                         user=await user_out(session, issued.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    await auth_service.logout(session, request.cookies.get(settings.refresh_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.refresh_cookie_name, path=f"{settings.api_prefix}/auth")
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: SessionDep) -> UserOut:
    return await user_out(session, user)


@router.get("/users", response_model=list[UserOut])
async def list_users(_: AdminUser, session: SessionDep) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [await user_out(session, u) for u in users]


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def set_role(user_id: uuid.UUID, body: RoleUpdate, _: AdminUser, session: SessionDep) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    user.role = body.role
    return await user_out(session, user)

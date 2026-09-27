from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.errors import AppError, ServiceUnavailable
from app.core.security import TokenError, create_signed_state, encrypt_secret, verify_signed_state
from app.db.models import GitHubConnection, User
from app.services.repository_service import github_token_for
from app.vcs import github

router = APIRouter(prefix="/github", tags=["github"])


@router.get("/status")
async def github_status(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    conn = await session.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    return {"configured": bool(settings.github_client_id and settings.github_client_secret),
            "connected": conn is not None, "login": conn.login if conn else None,
            "scopes": conn.scopes if conn else None}


@router.post("/oauth/start")
async def oauth_start(user: CurrentUser, settings: SettingsDep) -> dict[str, str]:
    if not (settings.github_client_id and settings.github_client_secret):
        raise ServiceUnavailable("GitHub OAuth is not configured on this server")
    state = create_signed_state({"uid": str(user.id), "purpose": "github_connect"})
    return {"authorize_url": github.authorize_url(settings, state)}


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str, session: SessionDep, settings: SettingsDep) -> RedirectResponse:
    """GitHub redirects the browser here. The signed `state` binds the callback to the user who started it."""
    target = f"{settings.frontend_url}/repositories"
    try:
        data = verify_signed_state(state)
        user = await session.get(User, uuid.UUID(data["uid"]))
        if user is None or data.get("purpose") != "github_connect":
            raise TokenError("unknown user")
        token_data = await github.exchange_code(settings, code)
        async with github.GitHubClient(token_data["access_token"], settings.github_api_url) as gh:
            profile = await gh.user()
    except (TokenError, github.GitHubError, KeyError, ValueError):
        return RedirectResponse(f"{target}?github=error", status_code=302)
    conn = await session.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    if conn is None:
        conn = GitHubConnection(user_id=user.id, github_user_id=profile["id"], login=profile["login"],
                                access_token_encrypted="")
        session.add(conn)
    conn.github_user_id = profile["id"]
    conn.login = profile["login"]
    conn.access_token_encrypted = encrypt_secret(token_data["access_token"])
    conn.scopes = token_data.get("scope", "")
    return RedirectResponse(f"{target}?github=connected", status_code=302)


@router.delete("/connection", status_code=204)
async def disconnect(user: CurrentUser, session: SessionDep) -> None:
    conn = await session.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    if conn is not None:
        await session.delete(conn)


@router.get("/repos")
async def list_github_repos(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> list[dict[str, Any]]:
    token = await github_token_for(session, user.id)
    if not token:
        raise AppError("GitHub account not connected")
    try:
        async with github.GitHubClient(token, settings.github_api_url) as gh:
            repos = await gh.list_repos()
    except github.GitHubError as exc:
        raise AppError(str(exc)) from exc
    return [{"full_name": r["full_name"], "private": r["private"], "default_branch": r.get("default_branch"),
             "description": r.get("description"), "language": r.get("language"), "pushed_at": r.get("pushed_at")}
            for r in repos]

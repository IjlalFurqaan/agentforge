"""Minimal GitHub REST client (OAuth exchange, repositories, pull requests, reviews)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def authorize_url(settings: Settings, state: str) -> str:
    if not settings.github_client_id:
        raise GitHubError("GitHub OAuth is not configured (GITHUB_CLIENT_ID)")
    query = urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": settings.github_oauth_scopes,
        "state": state,
        "allow_signup": "false",
    })
    return f"https://github.com/login/oauth/authorize?{query}"


async def exchange_code(settings: Settings, code: str) -> dict[str, Any]:
    if not (settings.github_client_id and settings.github_client_secret):
        raise GitHubError("GitHub OAuth is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                  "code": code, "redirect_uri": settings.github_oauth_redirect_uri},
        )
    data = resp.json()
    if resp.status_code != 200 or "access_token" not in data:
        raise GitHubError(f"OAuth exchange failed: {data.get('error_description') or data.get('error') or resp.status_code}")
    return data


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=api_url, timeout=30, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AgentForge"},
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise GitHubError(f"GitHub {method} {url} -> {resp.status_code}: {message}", resp.status_code)
        return resp.json() if resp.content else None

    async def user(self) -> dict[str, Any]:
        return await self._request("GET", "/user")

    async def list_repos(self, max_pages: int = 5) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = await self._request("GET", "/user/repos", params={
                "per_page": 100, "page": page, "sort": "pushed", "affiliation": "owner,collaborator,organization_member"})
            repos.extend(batch)
            if len(batch) < 100:
                break
        return repos

    async def get_repo(self, full_name: str) -> dict[str, Any]:
        return await self._request("GET", f"/repos/{full_name}")

    async def branch_protected(self, full_name: str, branch: str) -> bool:
        try:
            data = await self._request("GET", f"/repos/{full_name}/branches/{branch}")
        except GitHubError as exc:
            if exc.status == 404:
                return False
            raise
        return bool(data.get("protected"))

    async def create_pull_request(self, full_name: str, *, head: str, base: str, title: str, body: str,
                                  draft: bool = True) -> dict[str, Any]:
        return await self._request("POST", f"/repos/{full_name}/pulls", json={
            "title": title, "head": head, "base": base, "body": body, "draft": draft,
            "maintainer_can_modify": True})

    async def create_review(self, full_name: str, number: int, *, body: str,
                            comments: list[dict[str, Any]]) -> dict[str, Any]:
        # event=COMMENT: AgentForge never approves or requests changes on behalf of a human.
        return await self._request("POST", f"/repos/{full_name}/pulls/{number}/reviews", json={
            "body": body, "event": "COMMENT", "comments": comments})

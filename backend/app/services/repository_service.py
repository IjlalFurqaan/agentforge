"""Repository connections: validation of sources and on-demand analysis."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.security import decrypt_secret
from app.db.models import GitHubConnection, Repository, RepoSource, Role, User
from app.intelligence.analyzer import analyze_repository
from app.schemas.tasks import RepositoryCreate
from app.vcs import git_ops
from app.vcs.github import GitHubClient, GitHubError

_GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _assert_public_host(url: str) -> None:
    """SSRF guard: only https URLs whose host resolves exclusively to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AppError("Only https:// git URLs without embedded credentials are supported")
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise AppError(f"Cannot resolve host {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise AppError("Git URL resolves to a non-public address")


async def github_token_for(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    conn = await session.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user_id))
    return decrypt_secret(conn.access_token_encrypted) if conn else None


async def create_repository(session: AsyncSession, settings: Settings, user: User, body: RepositoryCreate) -> Repository:
    locator = body.locator.strip()
    name = body.name
    default_branch = body.default_branch or "main"
    if body.source == RepoSource.github:
        if not _GITHUB_NAME.match(locator):
            raise AppError("GitHub repositories must be given as owner/name")
        token = await github_token_for(session, user.id)
        if not token:
            raise AppError("Connect your GitHub account first")
        try:
            async with GitHubClient(token, settings.github_api_url) as gh:
                info = await gh.get_repo(locator)
        except GitHubError as exc:
            raise AppError(f"GitHub repository not accessible: {exc}") from exc
        default_branch = body.default_branch or info.get("default_branch", "main")
        name = name or info.get("full_name", locator)
    elif body.source == RepoSource.git_url:
        await asyncio.to_thread(_assert_public_host, locator)
        name = name or locator.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    else:
        if settings.is_production or settings.local_repos_root is None:
            raise Forbidden("Local repositories are disabled (set LOCAL_REPOS_ROOT in development)")
        path = Path(locator).expanduser().resolve()
        root = settings.local_repos_root.expanduser().resolve()
        if not path.is_relative_to(root) or not path.is_dir():
            raise Forbidden(f"Local repositories must be directories under {root}")
        locator = str(path)
        name = name or path.name
        if (path / ".git").exists() and not body.default_branch:
            try:
                default_branch = await git_ops.current_branch(path)
            except git_ops.GitError:
                pass
    existing = await session.scalar(select(Repository).where(
        Repository.owner_id == user.id, Repository.source == body.source, Repository.locator == locator))
    if existing:
        raise Conflict("This repository is already connected")
    repo = Repository(owner_id=user.id, name=name or locator, source=body.source, locator=locator,
                      default_branch=default_branch)
    session.add(repo)
    await session.flush()
    return repo


async def get_repository(session: AsyncSession, user: User, repo_id: uuid.UUID) -> Repository:
    repo = await session.get(Repository, repo_id)
    if repo is None or (repo.owner_id != user.id and user.role != Role.admin):
        raise NotFound("Repository not found")
    return repo


async def analyze(session: AsyncSession, settings: Settings, user: User, repo: Repository) -> Repository:
    """Fresh shallow clone into a scratch directory, then static analysis."""
    scratch = settings.workspaces_root / "_analysis" / str(repo.id)
    token = await github_token_for(session, repo.owner_id) if repo.source == RepoSource.github else None
    if repo.source == RepoSource.github:
        await git_ops.clone(f"https://github.com/{repo.locator}.git", scratch, branch=repo.default_branch, token=token,
                            depth=1)
    elif repo.source == RepoSource.git_url:
        await git_ops.clone(repo.locator, scratch, depth=1)
    else:
        source = Path(repo.locator)
        if (source / ".git").exists():
            await git_ops.clone(str(source), scratch, local=True)
        else:
            await git_ops.init_from_directory(source, scratch, (settings.git_author_name, settings.git_author_email))
    analysis = await asyncio.to_thread(analyze_repository, scratch, max_files=settings.max_repo_files,
                                       max_file_bytes=settings.max_file_bytes)
    repo.analysis = analysis.model_dump()
    repo.analysis_commit = await git_ops.head_commit(scratch)
    repo.analyzed_at = datetime.now(UTC)
    return repo


def summary_of(repo: Repository) -> dict | None:
    a = repo.analysis
    if not a:
        return None
    return {"primary_language": a.get("primary_language"), "file_count": a.get("file_count"),
            "total_lines": a.get("total_lines"), "frameworks": [f["name"] for f in a.get("frameworks", [])][:8],
            "test_files": a.get("testing", {}).get("test_files", 0)}

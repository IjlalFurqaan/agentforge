from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, EngineerUser, SessionDep, SettingsDep
from app.core.errors import AppError, NotFound
from app.db.models import Repository, Role
from app.schemas.tasks import RepositoryCreate, RepositoryOut
from app.services import repository_service
from app.vcs.git_ops import GitError

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _out(repo: Repository) -> RepositoryOut:
    out = RepositoryOut.model_validate(repo)
    out.summary = repository_service.summary_of(repo)
    return out


@router.get("", response_model=list[RepositoryOut])
async def list_repositories(user: CurrentUser, session: SessionDep) -> list[RepositoryOut]:
    query = select(Repository).order_by(Repository.created_at.desc())
    if user.role != Role.admin:
        query = query.where(Repository.owner_id == user.id)
    return [_out(r) for r in (await session.scalars(query)).all()]


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
async def connect_repository(body: RepositoryCreate, user: EngineerUser, session: SessionDep,
                             settings: SettingsDep) -> RepositoryOut:
    repo = await repository_service.create_repository(session, settings, user, body)
    return _out(repo)


@router.get("/{repo_id}", response_model=RepositoryOut)
async def get_repository(repo_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> RepositoryOut:
    return _out(await repository_service.get_repository(session, user, repo_id))


@router.post("/{repo_id}/analyze", response_model=RepositoryOut)
async def analyze_repository(repo_id: uuid.UUID, user: EngineerUser, session: SessionDep,
                             settings: SettingsDep) -> RepositoryOut:
    repo = await repository_service.get_repository(session, user, repo_id)
    try:
        repo = await repository_service.analyze(session, settings, user, repo)
    except GitError as exc:
        raise AppError(f"Could not fetch repository: {exc}") from exc
    return _out(repo)


@router.get("/{repo_id}/analysis")
async def get_analysis(repo_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    repo = await repository_service.get_repository(session, user, repo_id)
    if not repo.analysis:
        raise NotFound("Repository has not been analysed yet")
    return {"repository_id": str(repo.id), "commit": repo.analysis_commit,
            "analyzed_at": repo.analyzed_at.isoformat() if repo.analyzed_at else None, **repo.analysis}


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(repo_id: uuid.UUID, user: EngineerUser, session: SessionDep) -> None:
    repo = await repository_service.get_repository(session, user, repo_id)
    await session.delete(repo)

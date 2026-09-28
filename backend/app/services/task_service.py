"""Task lifecycle: creation, access control, approvals, cancellation and resumption."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound
from app.db.models import ACTIVE_STATUSES, Approval, ApprovalKind, ApprovalStatus, Role, Task, TaskStatus, User
from app.schemas.tasks import ApprovalDecision, TaskCreate
from app.services.repository_service import get_repository


async def create_task(session: AsyncSession, user: User, body: TaskCreate) -> Task:
    repo = await get_repository(session, user, body.repository_id)
    task = Task(owner_id=user.id, repository_id=repo.id, title=body.title.strip(), description=body.description.strip(),
                config=body.config.model_dump(mode="json"), status=TaskStatus.queued)
    session.add(task)
    await session.flush()
    return task


async def get_task(session: AsyncSession, user: User, task_id: uuid.UUID) -> Task:
    task = await session.get(Task, task_id)
    if task is None or (task.owner_id != user.id and user.role != Role.admin):
        raise NotFound("Task not found")
    return task


async def pending_approval(session: AsyncSession, task_id: uuid.UUID) -> Approval | None:
    return await session.scalar(select(Approval).where(Approval.task_id == task_id,
                                                       Approval.status == ApprovalStatus.pending)
                                .order_by(Approval.created_at.desc()).limit(1))


async def decide(session: AsyncSession, user: User, task: Task, approval_id: uuid.UUID,
                 decision: ApprovalDecision) -> Approval:
    approval = await session.get(Approval, approval_id)
    if approval is None or approval.task_id != task.id:
        raise NotFound("Approval not found")
    if approval.status != ApprovalStatus.pending or task.status != TaskStatus.awaiting_approval:
        raise Conflict("This approval has already been decided")
    resume: dict[str, Any]
    if approval.kind == ApprovalKind.clarification:
        if not decision.answers:
            raise AppError("Provide an answer for each question")
        resume = {"answers": [a.model_dump() for a in decision.answers], "comment": decision.comment}
        approval.status = ApprovalStatus.approved
    else:
        resume = {"approved": decision.approved, "comment": decision.comment}
        approval.status = ApprovalStatus.approved if decision.approved else ApprovalStatus.rejected
    approval.response_payload = resume
    approval.decided_by = user.id
    approval.decided_at = datetime.now(UTC)
    task.pending_resume = {"resume": resume, "approval_id": str(approval.id)}
    task.status = TaskStatus.queued
    return approval


async def cancel(session: AsyncSession, task: Task) -> Task:
    if task.status not in ACTIVE_STATUSES:
        raise Conflict(f"Task is already {task.status.value}")
    if task.status == TaskStatus.running:
        task.cancel_requested = True
    else:
        task.status = TaskStatus.cancelled
        task.finished_at = datetime.now(UTC)
        pending = await pending_approval(session, task.id)
        if pending:
            pending.status = ApprovalStatus.rejected
            pending.response_payload = {"cancelled": True}
    return task


async def resume(session: AsyncSession, task: Task) -> Task:
    if task.status != TaskStatus.failed:
        raise Conflict("Only failed tasks can be resumed")
    task.status = TaskStatus.queued
    task.pending_resume = {"retry": True}
    task.error = None
    task.finished_at = None
    task.cancel_requested = False
    return task

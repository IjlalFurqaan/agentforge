from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, EngineerUser, SessionDep
from app.core.errors import NotFound
from app.db.models import (
    AgentEvent,
    Approval,
    LLMCall,
    Repository,
    Role,
    SandboxExecution,
    Task,
    TaskArtifact,
    TaskStatus,
)
from app.db.session import sessionmaker
from app.orchestration.graph import graph_topology
from app.schemas.tasks import (
    ApprovalDecision,
    ApprovalOut,
    ArtifactOut,
    EventOut,
    ExecutionOut,
    LLMCallOut,
    TaskCreate,
    TaskOut,
)
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])
TERMINAL = {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled}


async def _task_out(session: SessionDep, task: Task) -> TaskOut:
    out = TaskOut.model_validate(task)
    repo = await session.get(Repository, task.repository_id)
    out.repository_name = repo.name if repo else None
    approval = await task_service.pending_approval(session, task.id)
    out.pending_approval = ApprovalOut.model_validate(approval) if approval else None
    return out


@router.get("/workflow")
async def workflow_topology() -> dict[str, Any]:
    return graph_topology()


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, user: EngineerUser, session: SessionDep) -> TaskOut:
    task = await task_service.create_task(session, user, body)
    return await _task_out(session, task)


@router.get("", response_model=list[TaskOut])
async def list_tasks(user: CurrentUser, session: SessionDep, status_filter: TaskStatus | None = Query(None, alias="status"),
                     limit: int = Query(50, ge=1, le=200)) -> list[TaskOut]:
    query = select(Task).order_by(desc(Task.created_at)).limit(limit)
    if user.role != Role.admin:
        query = query.where(Task.owner_id == user.id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    return [await _task_out(session, t) for t in (await session.scalars(query)).all()]


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> TaskOut:
    return await _task_out(session, await task_service.get_task(session, user, task_id))


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: uuid.UUID, user: EngineerUser, session: SessionDep) -> TaskOut:
    task = await task_service.cancel(session, await task_service.get_task(session, user, task_id))
    return await _task_out(session, task)


@router.post("/{task_id}/resume", response_model=TaskOut)
async def resume_task(task_id: uuid.UUID, user: EngineerUser, session: SessionDep) -> TaskOut:
    task = await task_service.resume(session, await task_service.get_task(session, user, task_id))
    return await _task_out(session, task)


@router.get("/{task_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(task_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> list[ApprovalOut]:
    await task_service.get_task(session, user, task_id)
    rows = await session.scalars(select(Approval).where(Approval.task_id == task_id).order_by(Approval.created_at))
    return [ApprovalOut.model_validate(a) for a in rows.all()]


@router.post("/{task_id}/approvals/{approval_id}", response_model=ApprovalOut)
async def decide_approval(task_id: uuid.UUID, approval_id: uuid.UUID, body: ApprovalDecision, user: EngineerUser,
                          session: SessionDep) -> ApprovalOut:
    task = await task_service.get_task(session, user, task_id)
    approval = await task_service.decide(session, user, task, approval_id, body)
    session.add(AgentEvent(
        task_id=task.id, seq=await _next_seq(session, task.id), agent=None, event_type="approval_decided",
        summary=f"{user.email} {'approved' if approval.status.value == 'approved' else 'rejected'} "
                f"the {approval.kind.value.replace('_', ' ')} gate",
        payload={"approval_id": str(approval.id), "decision": approval.response_payload, "by": user.email}))
    return ApprovalOut.model_validate(approval)


async def _next_seq(session: SessionDep, task_id: uuid.UUID) -> int:
    from sqlalchemy import func

    current = await session.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.task_id == task_id))
    return (current or 0) + 1


@router.get("/{task_id}/events", response_model=list[EventOut])
async def list_events(task_id: uuid.UUID, user: CurrentUser, session: SessionDep, after: int = Query(0, ge=0),
                      limit: int = Query(500, ge=1, le=2000)) -> list[EventOut]:
    await task_service.get_task(session, user, task_id)
    rows = await session.scalars(select(AgentEvent).where(AgentEvent.task_id == task_id, AgentEvent.seq > after)
                                 .order_by(AgentEvent.seq).limit(limit))
    return [EventOut.model_validate(e) for e in rows.all()]


@router.get("/{task_id}/stream")
async def stream_events(task_id: uuid.UUID, request: Request, user: CurrentUser, session: SessionDep,
                        after: int = Query(0, ge=0),
                        last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """Server-Sent Events tail of the audit log. Clients resume with Last-Event-ID (or ?after=)."""
    await task_service.get_task(session, user, task_id)
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        idle_polls = 0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            async with sessionmaker()() as s:
                events = (await s.scalars(select(AgentEvent).where(AgentEvent.task_id == task_id,
                                                                   AgentEvent.seq > cursor)
                                          .order_by(AgentEvent.seq).limit(200))).all()
                task = await s.get(Task, task_id)
                task_status = task.status if task else TaskStatus.failed
                snapshot = {"status": task_status.value, "current_node": task.current_node if task else None,
                            "cost_usd": task.cost_usd if task else 0, "input_tokens": task.input_tokens if task else 0,
                            "output_tokens": task.output_tokens if task else 0}
            for e in events:
                cursor = e.seq
                data = EventOut.model_validate(e).model_dump(mode="json")
                yield f"id: {e.seq}\nevent: agent_event\ndata: {json.dumps(data)}\n\n"
            yield f"event: task\ndata: {json.dumps(snapshot)}\n\n"
            if task_status in TERMINAL and not events:
                idle_polls += 1
                if idle_polls >= 2:
                    yield "event: end\ndata: {}\n\n"
                    return
            await asyncio.sleep(0.75)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{task_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(task_id: uuid.UUID, user: CurrentUser, session: SessionDep,
                         kind: str | None = None) -> list[ArtifactOut]:
    await task_service.get_task(session, user, task_id)
    query = select(TaskArtifact).where(TaskArtifact.task_id == task_id).order_by(TaskArtifact.id)
    if kind:
        query = query.where(TaskArtifact.kind == kind)
    return [ArtifactOut.model_validate(a) for a in (await session.scalars(query)).all()]


@router.get("/{task_id}/artifacts/{kind}/latest", response_model=ArtifactOut)
async def latest_artifact(task_id: uuid.UUID, kind: str, user: CurrentUser, session: SessionDep) -> ArtifactOut:
    await task_service.get_task(session, user, task_id)
    artifact = await session.scalar(select(TaskArtifact).where(TaskArtifact.task_id == task_id, TaskArtifact.kind == kind)
                                    .order_by(desc(TaskArtifact.id)).limit(1))
    if artifact is None:
        raise NotFound(f"No '{kind}' artifact yet")
    return ArtifactOut.model_validate(artifact)


@router.get("/{task_id}/executions", response_model=list[ExecutionOut])
async def list_executions(task_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> list[ExecutionOut]:
    await task_service.get_task(session, user, task_id)
    rows = await session.scalars(select(SandboxExecution).where(SandboxExecution.task_id == task_id)
                                 .order_by(SandboxExecution.id))
    return [ExecutionOut.model_validate(r) for r in rows.all()]


@router.get("/{task_id}/llm-calls", response_model=list[LLMCallOut])
async def list_llm_calls(task_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> list[LLMCallOut]:
    await task_service.get_task(session, user, task_id)
    rows = await session.scalars(select(LLMCall).where(LLMCall.task_id == task_id).order_by(LLMCall.id))
    return [LLMCallOut.model_validate(r) for r in rows.all()]

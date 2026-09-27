from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, SessionDep
from app.db.models import AgentEvent, LLMCall, Role, SandboxExecution, Task, TaskStatus
from app.schemas.common import _as_utc

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct * (len(ordered) - 1)))]


@router.get("/summary")
async def summary(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    scope = select(Task.id)
    if user.role != Role.admin:
        scope = scope.where(Task.owner_id == user.id)
    task_ids = scope

    by_status = dict((await session.execute(
        select(Task.status, func.count()).where(Task.id.in_(task_ids)).group_by(Task.status))).all())
    completed = by_status.get(TaskStatus.completed, 0)
    failed = by_status.get(TaskStatus.failed, 0)
    finished = completed + failed + by_status.get(TaskStatus.cancelled, 0)

    totals = (await session.execute(select(
        func.coalesce(func.sum(Task.cost_usd), 0.0), func.coalesce(func.sum(Task.input_tokens), 0),
        func.coalesce(func.sum(Task.output_tokens), 0)).where(Task.id.in_(task_ids)))).one()

    model_rows = (await session.execute(select(
        LLMCall.provider, LLMCall.model, func.count(), func.sum(LLMCall.input_tokens), func.sum(LLMCall.output_tokens),
        func.sum(LLMCall.cost_usd), func.avg(LLMCall.latency_ms),
        func.sum(case((LLMCall.status != "ok", 1), else_=0)),
    ).where(LLMCall.task_id.in_(task_ids)).group_by(LLMCall.provider, LLMCall.model))).all()
    latencies: dict[str, list[int]] = {}
    for provider, model, latency in (await session.execute(select(LLMCall.provider, LLMCall.model, LLMCall.latency_ms)
                                                           .where(LLMCall.task_id.in_(task_ids),
                                                                  LLMCall.status == "ok"))).all():
        latencies.setdefault(f"{provider}/{model}", []).append(latency)
    models = [{
        "model": f"{p}/{m}", "calls": calls, "input_tokens": int(inp or 0), "output_tokens": int(out or 0),
        "cost_usd": round(float(cost or 0), 4), "avg_latency_ms": int(avg or 0),
        "p95_latency_ms": _percentile(latencies.get(f"{p}/{m}", []), 0.95), "errors": int(errors or 0),
    } for p, m, calls, inp, out, cost, avg, errors in model_rows]

    agent_rows = (await session.execute(select(
        LLMCall.agent, func.count(), func.sum(LLMCall.input_tokens + LLMCall.output_tokens), func.sum(LLMCall.cost_usd),
    ).where(LLMCall.task_id.in_(task_ids)).group_by(LLMCall.agent))).all()
    agents = [{"agent": a, "llm_calls": c, "tokens": int(t or 0), "cost_usd": round(float(cost or 0), 4)}
              for a, c, t, cost in agent_rows]

    sandbox = (await session.execute(select(
        func.count(), func.sum(case((SandboxExecution.exit_code != 0, 1), else_=0)),
        func.sum(case((SandboxExecution.timed_out.is_(True), 1), else_=0)), func.avg(SandboxExecution.duration_ms),
    ).where(SandboxExecution.task_id.in_(task_ids)))).one()

    since = datetime.now(UTC) - timedelta(days=14)
    daily_rows = (await session.execute(select(Task.created_at, Task.status).where(
        Task.id.in_(task_ids), Task.created_at >= since))).all()
    daily: dict[str, dict[str, int]] = {}
    for created, st in daily_rows:
        day = created.date().isoformat()
        bucket = daily.setdefault(day, {"created": 0, "completed": 0, "failed": 0})
        bucket["created"] += 1
        if st in (TaskStatus.completed, TaskStatus.failed):
            bucket[st.value] += 1

    recent = (await session.scalars(select(AgentEvent).where(
        AgentEvent.task_id.in_(task_ids),
        AgentEvent.event_type.in_(["node_started", "message", "route", "approval_requested", "task_completed",
                                   "task_failed", "test_results", "pull_request"]))
        .order_by(AgentEvent.id.desc()).limit(25))).all()

    return {
        "tasks": {"by_status": {k.value: v for k, v in by_status.items()}, "total": sum(by_status.values()),
                  "active": sum(by_status.get(s, 0) for s in (TaskStatus.queued, TaskStatus.running,
                                                               TaskStatus.awaiting_approval)),
                  "success_rate": round(completed / (completed + failed), 4) if (completed + failed) else None,
                  "finished": finished},
        "usage": {"cost_usd": round(float(totals[0]), 4), "input_tokens": int(totals[1]), "output_tokens": int(totals[2])},
        "models": sorted(models, key=lambda m: -m["calls"]),
        "agents": sorted(agents, key=lambda a: -a["llm_calls"]),
        "sandbox": {"executions": sandbox[0] or 0, "non_zero_exits": int(sandbox[1] or 0),
                    "timeouts": int(sandbox[2] or 0), "avg_duration_ms": int(sandbox[3] or 0)},
        "daily": [{"day": d, **v} for d, v in sorted(daily.items())],
        "recent_activity": [{"task_id": str(e.task_id), "seq": e.seq, "agent": e.agent, "event_type": e.event_type,
                             "summary": e.summary, "created_at": _as_utc(e.created_at).isoformat()} for e in recent],
    }

"""Audit-trail writer: agent events, LLM usage, sandbox executions and artifacts.

Each write is its own short transaction so live viewers (SSE) see events immediately. A task is
only ever executed by one worker at a time (claimed through the queue), so the per-task sequence
counter can safely live in memory.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import func, select, update

from app.core.logging import get_logger
from app.core.telemetry import current_trace_id
from app.db.models import AgentEvent, LLMCall, SandboxExecution, Task, TaskArtifact
from app.db.session import session_scope
from app.llm.types import LLMResponse, ModelConfig
from app.sandbox.base import ExecResult

log = get_logger(__name__)


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value) - limit} chars truncated]"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value[:200]]
    return value


class EventRecorder:
    def __init__(self, task_id: uuid.UUID, next_seq: int) -> None:
        self.task_id = task_id
        self._seq = next_seq
        self._lock = asyncio.Lock()
        self.iteration = 0

    @classmethod
    async def for_task(cls, task_id: uuid.UUID) -> EventRecorder:
        async with session_scope() as s:
            current = await s.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.task_id == task_id))
        return cls(task_id, (current or 0) + 1)

    async def emit(self, event_type: str, summary: str, *, agent: str | None = None,
                   payload: dict[str, Any] | None = None) -> int:
        async with self._lock:
            seq = self._seq
            self._seq += 1
            async with session_scope() as s:
                s.add(AgentEvent(task_id=self.task_id, seq=seq, agent=agent, event_type=event_type,
                                 summary=summary[:500], payload=_truncate(payload or {}), iteration=self.iteration,
                                 trace_id=current_trace_id()))
        log.info("agent_event", task_id=str(self.task_id), seq=seq, event_type=event_type, agent=agent,
                 summary=summary[:200])
        return seq

    async def record_llm_call(self, *, agent: str, config: ModelConfig, response: LLMResponse | None,
                              cost: float | None, status: str, error: str | None, attempt: int) -> None:
        usage = response.usage if response else None
        async with session_scope() as s:
            s.add(LLMCall(
                task_id=self.task_id, agent=agent, provider=config.provider,
                model=(response.model if response else config.model) or config.model,
                input_tokens=usage.input_tokens if usage else 0, output_tokens=usage.output_tokens if usage else 0,
                cache_read_tokens=usage.cache_read_tokens if usage else 0,
                cache_write_tokens=usage.cache_write_tokens if usage else 0,
                cost_usd=cost or 0.0, latency_ms=response.latency_ms if response else 0, status=status,
                stop_reason=response.stop_reason if response else None, error=error, attempt=attempt,
            ))
            if usage:
                await s.execute(update(Task).where(Task.id == self.task_id).values(
                    input_tokens=Task.input_tokens + usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
                    output_tokens=Task.output_tokens + usage.output_tokens,
                    cost_usd=Task.cost_usd + (cost or 0.0),
                ))
        if status != "ok":
            await self.emit("retry" if status == "retry" else "error", f"LLM call failed ({config.label}): {error}",
                            agent=agent, payload={"model": config.label, "attempt": attempt, "error": error})
        else:
            await self.emit("llm_call", f"{config.label}: {usage.input_tokens if usage else 0} in / "
                            f"{usage.output_tokens if usage else 0} out tokens", agent=agent, payload={
                                "model": config.label, "latency_ms": response.latency_ms if response else 0,
                                "input_tokens": usage.input_tokens if usage else 0,
                                "output_tokens": usage.output_tokens if usage else 0,
                                "cache_read_tokens": usage.cache_read_tokens if usage else 0,
                                "cost_usd": cost, "stop_reason": response.stop_reason if response else None,
                                "priced": cost is not None})

    async def record_execution(self, *, agent: str | None, purpose: str, argv: list[str], workdir: str,
                               result: ExecResult) -> None:
        async with session_scope() as s:
            s.add(SandboxExecution(
                task_id=self.task_id, agent=agent, purpose=purpose, backend=result.backend, command=argv,
                workdir=workdir, network=result.network, exit_code=result.exit_code, timed_out=result.timed_out,
                duration_ms=result.duration_ms, stdout=result.stdout[-50_000:], stderr=result.stderr[-50_000:],
            ))
        status = "timed out" if result.timed_out else f"exit {result.exit_code}"
        await self.emit("sandbox_exec", f"$ {' '.join(argv)[:200]} -> {status} ({result.duration_ms} ms)",
                        agent=agent, payload={"argv": argv, "workdir": workdir, "purpose": purpose,
                                              "exit_code": result.exit_code, "timed_out": result.timed_out,
                                              "duration_ms": result.duration_ms, "backend": result.backend,
                                              "network": result.network, "stdout_tail": result.stdout[-1500:],
                                              "stderr_tail": result.stderr[-1500:]})

    async def save_artifact(self, kind: str, content: dict[str, Any], *, agent: str | None = None) -> None:
        async with session_scope() as s:
            s.add(TaskArtifact(task_id=self.task_id, kind=kind, iteration=self.iteration, agent=agent, content=content))

    async def set_current_node(self, node: str | None) -> None:
        async with session_scope() as s:
            await s.execute(update(Task).where(Task.id == self.task_id).values(current_node=node))

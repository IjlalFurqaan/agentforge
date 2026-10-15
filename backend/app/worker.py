"""Workflow worker: claims queued tasks from the database and runs them with bounded concurrency.

Run standalone with `python -m app.worker`, or embedded in the API process (WORKER_MODE=embedded).
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.telemetry import configure_tracing
from app.db.models import Task, TaskStatus
from app.db.session import dispose_engine, init_engine, session_scope
from app.llm.gateway import ProviderRegistry
from app.orchestration.checkpoint import open_checkpointer
from app.orchestration.runner import WorkflowRunner
from app.sandbox import build_sandbox

log = get_logger(__name__)
STALE_AFTER = timedelta(minutes=3)


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._stack = AsyncExitStack()
        self._loop_task: asyncio.Task[None] | None = None
        self._running: dict[uuid.UUID, tuple[asyncio.Task[None], asyncio.Event]] = {}
        self._stopping = asyncio.Event()
        self.registry = ProviderRegistry(settings)
        self.sandbox = build_sandbox(settings)
        self.runner: WorkflowRunner | None = None

    async def start(self) -> None:
        checkpointer = await self._stack.enter_async_context(open_checkpointer(self.settings))
        self.runner = WorkflowRunner(self.settings, checkpointer, self.registry, self.sandbox)
        await self._recover_stale()
        self._loop_task = asyncio.create_task(self._loop(), name="agentforge-worker")

    async def stop(self) -> None:
        self._stopping.set()
        for _, cancel in self._running.values():
            cancel.set()
        if self._running:
            await asyncio.wait([t for t, _ in self._running.values()], timeout=20)
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        await self.registry.aclose()
        await self._stack.aclose()

    async def _recover_stale(self) -> None:
        """Tasks left `running` by a crashed worker become `failed` (and remain resumable from checkpoints)."""
        cutoff = datetime.now(UTC) - STALE_AFTER
        async with session_scope() as s:
            result = await s.execute(
                update(Task).where(Task.status == TaskStatus.running,
                                   (Task.heartbeat_at.is_(None)) | (Task.heartbeat_at < cutoff))
                .values(status=TaskStatus.failed, error="Worker stopped unexpectedly; resume to continue",
                        worker_id=None).returning(Task.id))
            stale = result.scalars().all()
        if stale:
            log.warning("recovered_stale_tasks", count=len(stale))

    async def claim_next(self) -> uuid.UUID | None:
        async with session_scope() as s:
            candidate = select(Task.id).where(Task.status == TaskStatus.queued).order_by(Task.created_at).limit(1)
            now = datetime.now(UTC)
            result = await s.execute(
                update(Task).where(Task.id == candidate.scalar_subquery(), Task.status == TaskStatus.queued)
                .values(status=TaskStatus.running, worker_id=self.worker_id, heartbeat_at=now, error=None)
                .returning(Task.id, Task.started_at))
            row = result.first()
            if row is None:
                return None
            if row.started_at is None:
                await s.execute(update(Task).where(Task.id == row.id).values(started_at=now))
            return row.id

    async def _loop(self) -> None:
        last_heartbeat = 0.0
        while not self._stopping.is_set():
            try:
                while len(self._running) < self.settings.max_concurrent_workflows:
                    task_id = await self.claim_next()
                    if task_id is None:
                        break
                    cancel = asyncio.Event()
                    job = asyncio.create_task(self._execute(task_id, cancel), name=f"task-{task_id}")
                    self._running[task_id] = (job, cancel)
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat > 10:
                    await self._heartbeat()
                    last_heartbeat = now
            except Exception:
                log.exception("worker_loop_error")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.settings.worker_poll_interval_seconds)
            except TimeoutError:
                pass

    async def _heartbeat(self) -> None:
        if not self._running:
            return
        ids = list(self._running)
        async with session_scope() as s:
            await s.execute(update(Task).where(Task.id.in_(ids)).values(heartbeat_at=datetime.now(UTC)))
            cancelled = (await s.scalars(select(Task.id).where(Task.id.in_(ids), Task.cancel_requested.is_(True)))).all()
        for task_id in cancelled:
            self._running[task_id][1].set()

    async def _execute(self, task_id: uuid.UUID, cancel: asyncio.Event) -> None:
        assert self.runner is not None
        try:
            status = await self.runner.run(task_id, cancel)
            log.info("task_finished_segment", task_id=str(task_id), status=status.value)
        except Exception:
            log.exception("task_execution_crashed", task_id=str(task_id))
            async with session_scope() as s:
                await s.execute(update(Task).where(Task.id == task_id).values(
                    status=TaskStatus.failed, error="Internal worker error", worker_id=None))
        finally:
            self._running.pop(task_id, None)

    async def wait_idle(self, timeout: float = 600) -> None:
        """Test helper: wait until no task is running or queued."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            async with session_scope() as s:
                queued = await s.scalar(select(Task.id).where(Task.status == TaskStatus.queued).limit(1))
            if not self._running and queued is None:
                return
            await asyncio.sleep(0.2)
        raise TimeoutError("worker did not become idle")


async def main() -> None:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings)
    configure_tracing(settings)
    init_engine(settings)
    worker = Worker(settings)
    await worker.start()
    log.info("worker_started", worker_id=worker.worker_id, sandbox=settings.sandbox_backend)
    try:
        await asyncio.Event().wait()
    finally:
        await worker.stop()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())

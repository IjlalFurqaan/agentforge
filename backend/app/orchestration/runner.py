"""Runs (and resumes) one task's LangGraph workflow, translating graph outcomes into task status."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.agents.runtime import AgentRuntime, TaskCancelled, WorkflowLimits
from app.core.config import AGENT_NAMES, Settings
from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.core.telemetry import span
from app.db.models import Approval, ApprovalKind, GitHubConnection, RepoSource, Task, TaskStatus
from app.db.session import session_scope
from app.llm.gateway import LLMGateway, ProviderRegistry
from app.llm.types import BudgetExceeded, LLMError
from app.orchestration.graph import build_graph
from app.orchestration.nodes import TaskContext, WorkflowNodes
from app.sandbox import SandboxRunner
from app.services.events import EventRecorder
from app.services.workspace import workspace_for
from app.vcs import git_ops

log = get_logger(__name__)


def branch_name_for(title: str, task_id: uuid.UUID) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "change"
    return f"agentforge/{slug}-{task_id.hex[:8]}"


class WorkflowRunner:
    def __init__(self, settings: Settings, checkpointer: Any, registry: ProviderRegistry, sandbox: SandboxRunner) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        self.registry = registry
        self.sandbox = sandbox

    async def _prepare_workspace(self, task: Task, token: str | None, recorder: EventRecorder) -> tuple[str, str]:
        ws = workspace_for(self.settings.workspaces_root, str(task.id))
        repo = task.repository
        author = (self.settings.git_author_name, self.settings.git_author_email)
        if repo.source == RepoSource.github:
            await git_ops.clone(f"https://github.com/{repo.locator}.git", ws.repo, branch=repo.default_branch, token=token)
        elif repo.source == RepoSource.git_url:
            await git_ops.clone(repo.locator, ws.repo)
        else:
            from pathlib import Path

            source = Path(repo.locator)
            if (source / ".git").exists():
                await git_ops.clone(str(source), ws.repo, local=True)
            else:
                await git_ops.init_from_directory(source, ws.repo, author)
        branch = branch_name_for(task.title, task.id)
        await git_ops.create_branch(ws.repo, branch)
        base = await git_ops.head_commit(ws.repo)
        await recorder.emit("workspace", f"Cloned {repo.name} into an isolated workspace on branch {branch}",
                            payload={"branch": branch, "base_commit": base, "source": repo.source.value})
        return branch, base

    async def run(self, task_id: uuid.UUID, cancel_event: asyncio.Event | None = None) -> TaskStatus:
        async with session_scope() as s:
            task = await s.scalar(select(Task).options(selectinload(Task.repository)).where(Task.id == task_id))
            if task is None:
                raise LookupError(f"task {task_id} not found")
            pending = task.pending_resume
            task.pending_resume = None
            token = None
            if task.repository.source == RepoSource.github:
                conn = await s.scalar(select(GitHubConnection).where(GitHubConnection.user_id == task.owner_id))
                token = decrypt_secret(conn.access_token_encrypted) if conn else None
            s.expunge(task)
        recorder = await EventRecorder.for_task(task_id)
        fresh = task.base_commit is None  # no workspace yet: start from scratch

        try:
            if fresh:
                await recorder.emit("task_started", f"Task started: {task.title}",
                                    payload={"config": task.config, "repository": task.repository.name})
                branch, base = await self._prepare_workspace(task, token, recorder)
                async with session_scope() as s:
                    await s.execute(update(Task).where(Task.id == task_id).values(branch_name=branch, base_commit=base))
                task.branch_name, task.base_commit = branch, base
            else:
                await recorder.emit("task_resumed", "Workflow resumed from its last checkpoint",
                                    payload={"resume": bool(pending and "resume" in pending)})
        except Exception as exc:
            return await self._finish(task_id, recorder, TaskStatus.failed, f"Workspace preparation failed: {exc}")

        limits = WorkflowLimits.from_config(task.config or {}, self.settings)
        gateway = LLMGateway(self.settings, self.registry, task_models=(task.config or {}).get("models"),
                             sink=recorder, max_cost_usd=limits.max_cost_usd, spent_usd=task.cost_usd or 0.0)
        rt = AgentRuntime(task_id=task_id, settings=self.settings,
                          workspace=workspace_for(self.settings.workspaces_root, str(task_id)), sandbox=self.sandbox,
                          llm=gateway, events=recorder, limits=limits, config=task.config or {},
                          cancel_event=cancel_event or asyncio.Event())
        ctx = TaskContext(task_id=task_id, title=task.title, repo_source=task.repository.source.value,
                          repo_locator=task.repository.locator, default_branch=task.repository.default_branch,
                          base_commit=task.base_commit or "", branch_name=task.branch_name or "",
                          github_token=token, author=(self.settings.git_author_name, self.settings.git_author_email),
                          github_api_url=self.settings.github_api_url)
        try:
            labels = await gateway.preflight(AGENT_NAMES)
            await recorder.emit("models", f"Model preflight passed: {', '.join(labels)}", payload={"models": labels})
        except LLMError as exc:
            return await self._finish(task_id, recorder, TaskStatus.failed, f"Model configuration problem: {exc}")
        graph = build_graph(rt, WorkflowNodes(rt, ctx), self.checkpointer)
        config = {"configurable": {"thread_id": str(task_id)}, "recursion_limit": self.settings.graph_recursion_limit}
        has_checkpoint = bool((await graph.aget_state(config)).values)

        if pending and "resume" in pending:
            graph_input: Any = Command(resume=pending["resume"])
        elif not has_checkpoint:
            graph_input = {"task_id": str(task_id), "title": task.title, "request": task.description,
                           "config": task.config or {}, "messages": [], "resolved_message_ids": [], "route_log": []}
        else:
            graph_input = None  # retry from the last successful checkpoint

        try:
            with span("workflow.run", **{"agentforge.task_id": str(task_id)}):
                async with asyncio.timeout(limits.max_wall_clock_minutes * 60):
                    async for _ in graph.astream(graph_input, config, stream_mode="updates"):
                        rt.check_cancelled()
            snapshot = await graph.aget_state(config)
        except TaskCancelled:
            return await self._finish(task_id, recorder, TaskStatus.cancelled, "Cancelled by user")
        except BudgetExceeded as exc:
            return await self._finish(task_id, recorder, TaskStatus.failed, str(exc))
        except GraphRecursionError:
            return await self._finish(task_id, recorder, TaskStatus.failed,
                                      f"Workflow step limit ({self.settings.graph_recursion_limit}) reached")
        except TimeoutError:
            return await self._finish(task_id, recorder, TaskStatus.failed,
                                      f"Wall-clock limit of {limits.max_wall_clock_minutes} minutes reached")
        except LLMError as exc:
            return await self._finish(task_id, recorder, TaskStatus.failed, f"LLM error: {exc}")
        except Exception as exc:
            log.exception("workflow_failed", task_id=str(task_id))
            return await self._finish(task_id, recorder, TaskStatus.failed, f"{type(exc).__name__}: {exc}")

        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            kind = ApprovalKind(payload.get("kind", "pull_request"))
            async with session_scope() as s:
                approval = Approval(task_id=task_id, kind=kind, request_payload=payload)
                s.add(approval)
                await s.flush()
                await s.execute(update(Task).where(Task.id == task_id).values(
                    status=TaskStatus.awaiting_approval, current_node=snapshot.next[0] if snapshot.next else None))
                approval_id = str(approval.id)
            await recorder.emit("approval_requested", f"Human approval requested: {payload.get('title', kind.value)}",
                                payload={"approval_id": approval_id, "kind": kind.value})
            return TaskStatus.awaiting_approval

        values = snapshot.values
        status_note = (values.get("route") or {}).get("reason", "")
        return await self._finish(task_id, recorder, TaskStatus.completed, None, note=status_note)

    async def _finish(self, task_id: uuid.UUID, recorder: EventRecorder, status: TaskStatus, error: str | None,
                      note: str = "") -> TaskStatus:
        async with session_scope() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(
                status=status, error=error, finished_at=datetime.now(UTC), current_node=None, worker_id=None))
        summary = {TaskStatus.completed: f"Task completed{': ' + note if note else ''}",
                   TaskStatus.cancelled: "Task cancelled", TaskStatus.failed: f"Task failed: {error}"}[status]
        await recorder.emit(f"task_{status.value}", summary[:500], payload={"error": error})
        return status

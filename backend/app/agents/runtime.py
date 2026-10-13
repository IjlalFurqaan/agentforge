"""Everything an agent needs at run time, bundled so agents stay free of infrastructure code."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.telemetry import span
from app.llm.gateway import LLMGateway
from app.sandbox import ExecResult, SandboxRunner, validate_command
from app.services.events import EventRecorder
from app.services.workspace import Workspace


class TaskCancelled(Exception):
    pass


@dataclass
class WorkflowLimits:
    max_dev_iterations: int
    max_arch_revisions: int
    max_tool_calls: int
    max_cost_usd: float
    max_wall_clock_minutes: int

    @classmethod
    def from_config(cls, config: dict[str, Any], settings: Settings) -> WorkflowLimits:
        limits = config.get("limits", {}) or {}
        return cls(
            max_dev_iterations=int(limits.get("max_dev_iterations", settings.default_max_dev_iterations)),
            max_arch_revisions=int(limits.get("max_arch_revisions", settings.default_max_arch_revisions)),
            max_tool_calls=int(limits.get("max_tool_calls", settings.default_max_tool_calls)),
            max_cost_usd=float(limits.get("max_cost_usd", settings.default_max_cost_usd)),
            max_wall_clock_minutes=int(limits.get("max_wall_clock_minutes", settings.default_max_wall_clock_minutes)),
        )


@dataclass
class AgentRuntime:
    task_id: uuid.UUID
    settings: Settings
    workspace: Workspace
    sandbox: SandboxRunner
    llm: LLMGateway
    events: EventRecorder
    limits: WorkflowLimits
    config: dict[str, Any] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled("Task cancelled by user")

    async def emit(self, event_type: str, summary: str, *, agent: str | None = None,
                   payload: dict[str, Any] | None = None) -> None:
        await self.events.emit(event_type, summary, agent=agent, payload=payload)

    async def message(self, sender: str, recipient: str, kind: str, content: str, *, refs: dict[str, Any] | None = None,
                      message_id: str | None = None) -> dict[str, Any]:
        """Create an agent-to-agent message (also recorded in the audit log)."""
        msg = {"id": message_id or f"m-{uuid.uuid4().hex[:8]}", "from": sender, "to": recipient, "kind": kind,
               "content": content, "refs": refs or {}, "iteration": self.events.iteration}
        await self.emit("message", f"{sender} → {recipient} [{kind}]: {content[:300]}", agent=sender, payload=msg)
        return msg

    async def execute(self, argv: list[str], *, agent: str | None, purpose: str = "run", workdir: str = ".",
                      timeout_s: int | None = None, allow_network: bool = False) -> ExecResult:
        """Run a command inside the sandbox. Raises PolicyViolation / SandboxUnavailable."""
        cmd = validate_command(argv, workdir=workdir, purpose=purpose, allow_network=allow_network)
        with span("sandbox.exec", **{"agentforge.agent": agent, "sandbox.backend": self.sandbox.name,
                                     "sandbox.purpose": purpose, "sandbox.network": cmd.network}):
            result = await self.sandbox.run(cmd, self.workspace.sandbox_paths,
                                            timeout_s=timeout_s or self.settings.sandbox_timeout_seconds)
        await self.events.record_execution(agent=agent, purpose=purpose, argv=list(cmd.argv), workdir=cmd.workdir,
                                           result=result)
        return result

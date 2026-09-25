from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import AGENT_NAMES
from app.db.models import ApprovalKind, ApprovalStatus, RepoSource, TaskStatus
from app.schemas.common import UTCDateTime

# --- repositories ----------------------------------------------------------------------------------


class RepositoryCreate(BaseModel):
    source: RepoSource
    locator: str = Field(min_length=1, max_length=1000,
                         description="github: owner/name · git_url: https URL · local: absolute path")
    name: str | None = Field(default=None, max_length=200)
    default_branch: str | None = Field(default=None, max_length=200)


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    source: RepoSource
    locator: str
    default_branch: str
    analyzed_at: UTCDateTime | None
    created_at: UTCDateTime
    summary: dict[str, Any] | None = None


# --- tasks -----------------------------------------------------------------------------------------


class ModelSelection(BaseModel):
    provider: Literal["anthropic", "openai", "ollama"]
    model: str = Field(min_length=1, max_length=100)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    context_window: int | None = Field(default=None, ge=2048, le=1_000_000)


class Limits(BaseModel):
    max_dev_iterations: int = Field(default=4, ge=1, le=10)
    max_arch_revisions: int = Field(default=2, ge=0, le=5)
    max_tool_calls: int = Field(default=40, ge=5, le=200)
    max_cost_usd: float = Field(default=5.0, ge=0.0, le=500.0)
    max_wall_clock_minutes: int = Field(default=60, ge=5, le=480)


class ApprovalPreferences(BaseModel):
    clarifications: bool = True
    plan: bool = False
    pull_request: Literal["require", "skip"] = "require"


class TaskConfig(BaseModel):
    models: dict[str, ModelSelection] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    approvals: ApprovalPreferences = Field(default_factory=ApprovalPreferences)

    @field_validator("models")
    @classmethod
    def _known_agents(cls, value: dict[str, ModelSelection]) -> dict[str, ModelSelection]:
        unknown = set(value) - {*AGENT_NAMES, "default"}
        if unknown:
            raise ValueError(f"unknown agents in model config: {sorted(unknown)}")
        return value


class TaskCreate(BaseModel):
    repository_id: uuid.UUID
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=10, max_length=20_000)
    config: TaskConfig = Field(default_factory=TaskConfig)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    repository_name: str | None = None
    title: str
    description: str
    config: dict[str, Any]
    status: TaskStatus
    current_node: str | None
    branch_name: str | None
    base_commit: str | None
    pr_url: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    finished_at: UTCDateTime | None
    pending_approval: ApprovalOut | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    agent: str | None
    event_type: str
    summary: str
    payload: dict[str, Any]
    iteration: int
    created_at: UTCDateTime


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ApprovalKind
    status: ApprovalStatus
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None
    decided_at: UTCDateTime | None
    created_at: UTCDateTime


class ClarificationAnswer(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=5000)


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=5000)
    answers: list[ClarificationAnswer] = Field(default_factory=list)


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    iteration: int
    agent: str | None
    content: dict[str, Any]
    created_at: UTCDateTime


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent: str | None
    purpose: str
    backend: str
    command: list[Any]
    workdir: str
    network: bool
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    created_at: UTCDateTime


class LLMCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    stop_reason: str | None
    error: str | None
    created_at: UTCDateTime


TaskOut.model_rebuild()

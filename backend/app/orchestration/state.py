"""Shared LangGraph workflow state. Values are plain JSON so checkpoints are portable."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class WorkflowState(TypedDict, total=False):
    # immutable task inputs
    task_id: str
    title: str
    request: str
    config: dict[str, Any]

    # repository understanding
    repo_brief: str
    readme: str | None
    testing: dict[str, Any]
    infrastructure: dict[str, Any]
    dependencies: list[dict[str, Any]]

    # agent outputs (latest version)
    requirements: dict[str, Any]
    clarifications: list[dict[str, Any]]
    clarification_rounds: int
    design: dict[str, Any]
    design_version: int
    design_feedback: list[str]
    plan_approved: bool
    environment: dict[str, Any]
    implementation: dict[str, Any]
    changed_files: list[dict[str, Any]]
    diff_stat: str
    qa: dict[str, Any]
    security: dict[str, Any]
    security_withdrawn: list[str]
    review: dict[str, Any]
    deployment: dict[str, Any]
    report: dict[str, Any]
    pr: dict[str, Any]

    # explicit agent-to-agent communication (append-only)
    messages: Annotated[list[dict[str, Any]], operator.add]
    resolved_message_ids: Annotated[list[str], operator.add]

    # control flow
    dev_iterations: int
    arch_revisions: int
    route: dict[str, Any]
    route_log: Annotated[list[dict[str, Any]], operator.add]

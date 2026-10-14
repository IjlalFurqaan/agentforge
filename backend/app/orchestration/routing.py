"""Pure routing decisions. Every function returns (next_node, human-readable reason)."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

from app.agents.runtime import WorkflowLimits

Route = tuple[str, str]


def approvals(state: dict[str, Any]) -> dict[str, Any]:
    defaults = {"clarifications": True, "plan": False, "pull_request": "require"}
    return {**defaults, **(state.get("config", {}).get("approvals") or {})}


def after_product_manager(state: dict[str, Any]) -> Route:
    blocking = [a for a in state.get("requirements", {}).get("ambiguities", []) if a.get("blocking")]
    if blocking and approvals(state)["clarifications"] and state.get("clarification_rounds", 0) < 1:
        return "clarify", f"{len(blocking)} blocking question(s) need a human answer"
    if blocking:
        return "architect", f"proceeding with documented assumptions for {len(blocking)} blocking question(s)"
    return "architect", "requirements complete"


def after_architect(state: dict[str, Any]) -> Route:
    if approvals(state)["plan"]:
        return "plan_review", "plan approval required before implementation"
    return "setup_env", "design ready; plan approval not required"


def after_plan_review(state: dict[str, Any], limits: WorkflowLimits) -> Route:
    if state.get("plan_approved"):
        return "setup_env", "plan approved by a human"
    if state.get("arch_revisions", 0) < limits.max_arch_revisions:
        return "architect", "plan rejected; architect revising"
    return END, "plan rejected and architecture revision limit reached"


def after_qa(state: dict[str, Any], limits: WorkflowLimits) -> Route:
    qa = state.get("qa", {})
    run = qa.get("run", {})
    if qa.get("passed"):
        return "security", f"{run.get('passed', 0)}/{run.get('total', 0)} tests passed"
    if not run.get("executed"):
        return "security", f"tests could not be executed ({run.get('reason')}); continuing with that limitation"
    if state.get("dev_iterations", 0) < limits.max_dev_iterations:
        return "developer", (f"tests failed ({run.get('failed', 0)} failed, {run.get('errors', 0)} errors); "
                             "returning to developer")
    return "security", "tests still failing but the developer iteration limit is reached"


def after_security(state: dict[str, Any], limits: WorkflowLimits) -> Route:
    blocking = state.get("security", {}).get("blocking", [])
    if blocking and state.get("dev_iterations", 0) < limits.max_dev_iterations:
        return "developer", f"{len(blocking)} blocking security issue(s); returning findings to developer"
    if blocking:
        return "reviewer", f"{len(blocking)} blocking issue(s) remain but the iteration limit is reached"
    return "reviewer", "no blocking security issues"


def after_review(state: dict[str, Any], limits: WorkflowLimits) -> Route:
    verdict = state.get("review", {}).get("verdict")
    dev, arch = state.get("dev_iterations", 0), state.get("arch_revisions", 0)
    if verdict == "request_redesign" and arch < limits.max_arch_revisions:
        return "architect", "reviewer requested an architectural redesign"
    if verdict in {"request_changes", "request_redesign"} and dev < limits.max_dev_iterations:
        return "developer", "reviewer requested changes"
    if verdict != "approve":
        return "deployment", f"review verdict '{verdict}' unresolved; iteration limit reached"
    return "deployment", "review approved"


def after_pr_review(state: dict[str, Any]) -> Route:
    if approvals(state)["pull_request"] == "skip":
        return END, "pull request creation disabled for this task"
    if state.get("pr", {}).get("approved"):
        return "publish_pr", "pull request approved by a human"
    return END, "pull request rejected by a human"

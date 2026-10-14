"""LangGraph construction: nodes, conditional edges, retry policies, instrumentation."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from sqlalchemy.exc import OperationalError

from app.agents.runtime import AgentRuntime
from app.core.telemetry import span
from app.llm.types import LLMTransientError
from app.orchestration.nodes import WorkflowNodes
from app.orchestration.state import WorkflowState

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# node -> agent responsible (None = platform step)
NODE_AGENTS: dict[str, str | None] = {
    "analyze_repo": "architect", "product_manager": "product_manager", "clarify": None, "architect": "architect",
    "plan_review": None, "setup_env": None, "developer": "developer", "qa": "qa", "security": "security",
    "reviewer": "reviewer", "deployment": "deployment", "final_report": None, "pr_review": None, "publish_pr": None,
}
NODE_LABELS = {
    "analyze_repo": "Repository analysis", "product_manager": "Requirements analysis", "clarify": "Clarification gate",
    "architect": "Technical design", "plan_review": "Plan approval gate", "setup_env": "Sandbox environment setup",
    "developer": "Implementation", "qa": "Testing", "security": "Security review", "reviewer": "Code review",
    "deployment": "Deployment readiness", "final_report": "Final report", "pr_review": "Pull request approval gate",
    "publish_pr": "Pull request publication",
}

TRANSIENT_ERRORS = (LLMTransientError, OperationalError, httpx.TransportError)


def instrument(name: str, fn: NodeFn, rt: AgentRuntime) -> NodeFn:
    agent = NODE_AGENTS[name]

    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        rt.check_cancelled()
        await rt.events.set_current_node(name)
        await rt.emit("node_started", f"{NODE_LABELS[name]} started", agent=agent,
                      payload={"node": name, "dev_iteration": state.get("dev_iterations", 0)})
        started = time.perf_counter()
        with span(f"workflow.{name}", **{"agentforge.task_id": str(rt.task_id), "agentforge.node": name}):
            try:
                update = await fn(state)
            except GraphBubbleUp:  # interrupt() for human-in-the-loop: not an error
                await rt.emit("awaiting_human", f"{NODE_LABELS[name]}: waiting for a human decision", agent=agent,
                              payload={"node": name})
                raise
            except Exception as exc:
                await rt.emit("error", f"{NODE_LABELS[name]} failed: {type(exc).__name__}: {str(exc)[:300]}",
                              agent=agent, payload={"node": name, "error_type": type(exc).__name__,
                                                    "message": str(exc)[:2000]})
                raise
        await rt.emit("node_completed", f"{NODE_LABELS[name]} completed", agent=agent,
                      payload={"node": name, "duration_ms": int((time.perf_counter() - started) * 1000),
                               "route": update.get("route") if isinstance(update, dict) else None})
        return update

    wrapper.__name__ = name
    return wrapper


def _route(state: dict[str, Any]) -> str:
    return state["route"]["to"]


def build_graph(rt: AgentRuntime, nodes: WorkflowNodes, checkpointer: Any) -> Any:
    g = StateGraph(WorkflowState)
    retry = RetryPolicy(max_attempts=2, initial_interval=2.0, retry_on=TRANSIENT_ERRORS)
    for name in NODE_AGENTS:
        g.add_node(name, instrument(name, getattr(nodes, name), rt), retry_policy=retry)

    g.add_edge(START, "analyze_repo")
    g.add_edge("analyze_repo", "product_manager")
    g.add_conditional_edges("product_manager", _route, {"clarify": "clarify", "architect": "architect"})
    g.add_edge("clarify", "product_manager")
    g.add_conditional_edges("architect", _route, {"plan_review": "plan_review", "setup_env": "setup_env"})
    g.add_conditional_edges("plan_review", _route, {"architect": "architect", "setup_env": "setup_env", END: END})
    g.add_edge("setup_env", "developer")
    g.add_edge("developer", "qa")
    g.add_conditional_edges("qa", _route, {"developer": "developer", "security": "security"})
    g.add_conditional_edges("security", _route, {"developer": "developer", "reviewer": "reviewer"})
    g.add_conditional_edges("reviewer", _route, {"developer": "developer", "architect": "architect",
                                                 "deployment": "deployment"})
    g.add_edge("deployment", "final_report")
    g.add_edge("final_report", "pr_review")
    g.add_conditional_edges("pr_review", _route, {"publish_pr": "publish_pr", END: END})
    g.add_edge("publish_pr", END)
    return g.compile(checkpointer=checkpointer)


def graph_topology() -> dict[str, Any]:
    """Static description of the workflow for the UI."""
    edges = [
        ("analyze_repo", "product_manager", None), ("product_manager", "clarify", "blocking questions"),
        ("product_manager", "architect", None), ("clarify", "product_manager", "answers"),
        ("architect", "plan_review", "plan approval on"), ("architect", "setup_env", None),
        ("plan_review", "architect", "rejected"), ("plan_review", "setup_env", "approved"),
        ("setup_env", "developer", None), ("developer", "qa", None), ("qa", "developer", "tests failed"),
        ("qa", "security", "tests passed"), ("security", "developer", "blocking findings"),
        ("security", "reviewer", None), ("reviewer", "developer", "changes requested"),
        ("reviewer", "architect", "redesign requested"), ("reviewer", "deployment", "approved"),
        ("deployment", "final_report", None), ("final_report", "pr_review", None),
        ("pr_review", "publish_pr", "approved"),
    ]
    return {"nodes": [{"id": n, "label": NODE_LABELS[n], "agent": a} for n, a in NODE_AGENTS.items()],
            "edges": [{"source": s, "target": t, "label": label} for s, t, label in edges]}

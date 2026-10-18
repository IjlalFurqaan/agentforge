"""End-to-end workflow tests: real graph, real workspace/git, real sandbox execution, scripted model."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import Task, TaskStatus
from app.db.session import session_scope
from app.worker import Worker
from tests.conftest import register_and_login, requires_sandbox
from tests.fakes import ScriptedProvider

pytestmark = [requires_sandbox, pytest.mark.sandbox]


async def wait_for_status(task_id: str, wanted: set[TaskStatus], timeout: float = 600) -> Task:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with session_scope() as s:
            task = await s.scalar(select(Task).where(Task.id == __import__("uuid").UUID(task_id)))
            if task and task.status in wanted:
                return task
            if task and task.status == TaskStatus.failed and TaskStatus.failed not in wanted:
                raise AssertionError(f"task failed: {task.error}")
        await asyncio.sleep(0.3)
    raise TimeoutError(f"task did not reach {wanted}")


async def start_worker(settings, provider: ScriptedProvider) -> Worker:
    worker = Worker(settings)
    worker.registry.register(provider)
    await worker.start()
    return worker


async def create_demo_task(client, token: str, demo_repo, approvals: dict) -> str:
    h = {"Authorization": f"Bearer {token}"}
    repo = await client.post("/api/repositories", json={"source": "local", "locator": str(demo_repo)}, headers=h)
    assert repo.status_code == 201, repo.text
    r = await client.post("/api/tasks", headers=h, json={
        "repository_id": repo.json()["id"], "title": "Project statistics endpoint",
        "description": "Add GET /projects/{id}/stats returning item counts per status, with tests.",
        "config": {"models": {"default": {"provider": "openai", "model": "placeholder"}},
                   "approvals": approvals, "limits": {"max_dev_iterations": 3, "max_cost_usd": 10}}})
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]
    # Point every agent at the scripted test double (the API only accepts real providers).
    async with session_scope() as s:
        task = await s.get(Task, __import__("uuid").UUID(task_id))
        task.config = {**task.config, "models": {"default": {"provider": "scripted", "model": "scripted-1"}}}
    return task_id


async def approve(client, token: str, task_id: str, expected_kind: str, **body) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    task = (await client.get(f"/api/tasks/{task_id}", headers=h)).json()
    approval = task["pending_approval"]
    assert approval and approval["kind"] == expected_kind, task
    r = await client.post(f"/api/tasks/{task_id}/approvals/{approval['id']}", headers=h,
                          json={"approved": True, "comment": "", **body})
    assert r.status_code == 200, r.text
    return approval


async def test_full_workflow_with_revision_loop_and_human_gates(client, settings, demo_repo):
    token = await register_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    provider = ScriptedProvider()
    worker = await start_worker(settings, provider)
    try:
        task_id = await create_demo_task(client, token, demo_repo,
                                         {"plan": True, "clarifications": True, "pull_request": "require"})
        await wait_for_status(task_id, {TaskStatus.awaiting_approval})
        plan = await approve(client, token, task_id, "plan")
        assert plan["request_payload"]["design"]["affected_files"][0]["path"] == "app/routers/projects.py"

        await wait_for_status(task_id, {TaskStatus.awaiting_approval}, timeout=900)
        pr_gate = await approve(client, token, task_id, "pull_request")
        assert pr_gate["request_payload"]["status"] == "ready_for_review"
        task = await wait_for_status(task_id, {TaskStatus.completed})
    finally:
        await worker.stop()

    events = (await client.get(f"/api/tasks/{task_id}/events", headers=h)).json()
    started = [e["payload"]["node"] for e in events if e["event_type"] == "node_started"]
    # QA failure sent the work back to the developer, then everything passed. Gate nodes appear twice:
    # once when they pause for the human, once when the graph resumes with the decision.
    assert started == ["analyze_repo", "product_manager", "architect", "plan_review", "plan_review", "setup_env",
                       "developer", "qa", "developer", "qa", "security", "reviewer", "deployment", "final_report",
                       "pr_review", "pr_review", "publish_pr"]
    results = [e["payload"] for e in events if e["event_type"] == "test_results"]
    assert results[0]["failed"] >= 1 and not results[0]["succeeded"]
    assert results[-1]["succeeded"] and results[-1]["failed"] == 0 and results[-1]["passed"] == 7
    messages = [e["payload"] for e in events if e["event_type"] == "message"]
    assert any(m["from"] == "qa" and m["to"] == "developer" and m["kind"] == "feedback" for m in messages)
    assert any(m["from"] == "developer" and m["to"] == "qa" and m["content"].startswith("FIXED") for m in messages)
    assert any(e["event_type"] == "route" and e["payload"]["to"] == "developer" for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)

    executions = (await client.get(f"/api/tasks/{task_id}/executions", headers=h)).json()
    assert {x["backend"] for x in executions} == {"seatbelt"}
    assert any(x["purpose"] == "install" and x["network"] for x in executions)
    assert all(not x["network"] for x in executions if x["purpose"] in {"test", "run", "scan"})

    report = (await client.get(f"/api/tasks/{task_id}/artifacts/final_report/latest", headers=h)).json()["content"]
    assert report["status"] == "ready_for_review"
    assert {f["path"] for f in report["files_modified"]} == {"app/routers/projects.py", "tests/test_stats.py"}
    assert all(a["status"] == "verified" for a in report["requirements"]["acceptance"])
    assert "bandit" in report["security"]["tools_run"] and report["commit"]
    assert report["iterations"]["developer"] == 2

    pr = (await client.get(f"/api/tasks/{task_id}/artifacts/pull_request/latest", headers=h)).json()["content"]
    assert pr["status"] == "not_pushed" and "not connected through GitHub" in pr["reason"]

    calls = (await client.get(f"/api/tasks/{task_id}/llm-calls", headers=h)).json()
    assert len(calls) == len(provider.calls) and task.input_tokens == 1000 * len(calls)
    metrics = (await client.get("/api/metrics/summary", headers=h)).json()
    assert metrics["tasks"]["by_status"]["completed"] == 1 and metrics["models"][0]["model"] == "scripted/scripted-1"

    # the fix is really committed on the agent's branch in the isolated workspace
    from app.vcs import git_ops

    repo = settings.workspaces_root / task_id / "repo"
    assert (await git_ops.current_branch(repo)).startswith("agentforge/project-statistics-endpoint-")
    assert "counts[item.status] += 1" in (repo / "app" / "routers" / "projects.py").read_text()
    # ... and the user's source checkout was never touched
    assert "stats" not in (demo_repo / "app" / "routers" / "projects.py").read_text()


async def test_clarification_gate_failure_and_resume(client, settings, demo_repo):
    token = await register_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    provider = ScriptedProvider(blocking_question=True, fail_architect_once=True)
    worker = await start_worker(settings, provider)
    try:
        task_id = await create_demo_task(client, token, demo_repo, {"plan": False, "pull_request": "skip"})
        await wait_for_status(task_id, {TaskStatus.awaiting_approval})
        await approve(client, token, task_id, "clarification",
                      answers=[{"question": "Include archived items?", "answer": "There is no archived state."}])
        failed = await wait_for_status(task_id, {TaskStatus.failed})
        assert "simulated provider outage" in failed.error
        r = await client.post(f"/api/tasks/{task_id}/resume", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "queued"
        await wait_for_status(task_id, {TaskStatus.completed}, timeout=900)
    finally:
        await worker.stop()
    events = (await client.get(f"/api/tasks/{task_id}/events", headers=h)).json()
    types = [e["event_type"] for e in events]
    assert "task_resumed" in types and types.count("task_started") == 1
    pm_runs = [e for e in events if e["event_type"] == "node_started" and e["payload"]["node"] == "product_manager"]
    assert len(pm_runs) == 2  # re-run with the human's answers
    # analysis ran once: resume continued from the checkpoint instead of starting over
    assert sum(1 for e in events if e["event_type"] == "node_started" and e["payload"]["node"] == "analyze_repo") == 1


async def test_resume_after_failure_before_first_checkpoint(client, settings, demo_repo):
    token = await register_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    worker = await start_worker(settings, ScriptedProvider(fail_preflight_once=True))
    try:
        task_id = await create_demo_task(client, token, demo_repo, {"pull_request": "skip"})
        failed = await wait_for_status(task_id, {TaskStatus.failed})
        assert failed.error.startswith("Model configuration problem") and failed.base_commit
        assert (await client.post(f"/api/tasks/{task_id}/resume", headers=h)).status_code == 200
        await wait_for_status(task_id, {TaskStatus.completed}, timeout=900)
    finally:
        await worker.stop()
    report = (await client.get(f"/api/tasks/{task_id}/artifacts/final_report/latest", headers=h)).json()["content"]
    assert report["tests"]["succeeded"]


async def test_cancel_queued_task_and_access_control(client, settings, demo_repo):
    owner = await register_and_login(client)
    other = await register_and_login(client, "other@example.com")
    task_id = await create_demo_task(client, owner, demo_repo, {})
    assert (await client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {other}"})).status_code == 404
    r = await client.post(f"/api/tasks/{task_id}/cancel", headers={"Authorization": f"Bearer {owner}"})
    assert r.json()["status"] == "cancelled"

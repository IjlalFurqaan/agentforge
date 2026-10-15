"""Scripted LLM provider — a TEST DOUBLE. It lives only in tests/ and is never registered by the app.

It plays each agent role with a fixed script so the real orchestration, sandbox, git and test-execution
paths can be exercised deterministically. Every outcome the platform records (test pass/fail, diffs,
scanner results) still comes from real execution; only the "model's" decisions are scripted.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.llm.providers.base import LLMProvider
from app.llm.types import LLMRequest, LLMResponse, ModelConfig, TextBlock, ToolCall, Usage

BUGGY_ENDPOINT = '''@router.get("/projects/{project_id}/stats")
def project_stats(project_id: int, db: Session = Depends(get_db)) -> dict[str, int]:
    _project_or_404(db, project_id)
    counts = {"todo": 0, "in_progress": 0, "done": 0}
    for item in db.scalars(select(Item).where(Item.project_id == project_id)):
        counts["todo"] += 1
    return counts


'''
STATS_TESTS = '''def test_stats_counts_by_status(client):
    project = client.post("/projects", json={"name": "Stats"}).json()
    first = client.post(f"/projects/{project['id']}/items", json={"title": "a"}).json()
    client.post(f"/projects/{project['id']}/items", json={"title": "b"})
    client.patch(f"/items/{first['id']}", json={"status": "done"})
    assert client.get(f"/projects/{project['id']}/stats").json() == {"todo": 1, "in_progress": 0, "done": 1}


def test_stats_missing_project(client):
    assert client.get("/projects/999/stats").status_code == 404
'''


def _role(system: str) -> str:
    m = re.search(r"# Role: ([A-Za-z ]+?)(?: \(results analysis\))?\n", system)
    role = m.group(1).strip().lower().replace(" ", "_") if m else "unknown"
    return "qa_analysis" if "(results analysis)" in system else role


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, *, blocking_question: bool = False, fail_architect_once: bool = False,
                 fail_preflight_once: bool = False) -> None:
        self.blocking_question = blocking_question
        self.fail_architect_once = fail_architect_once
        self.fail_preflight_once = fail_preflight_once
        self.calls: list[str] = []

    async def preflight(self, model: str) -> None:
        if self.fail_preflight_once:
            self.fail_preflight_once = False
            from app.llm.types import LLMError

            raise LLMError(f"model {model} not loaded")

    async def generate(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        role = _role(request.system)
        self.calls.append(role)
        step = sum(1 for m in request.messages if m.role == "assistant")
        prompt = request.messages[0].text
        handler = getattr(self, f"_{role}")
        result = handler(step, prompt, request)
        if isinstance(result, Exception):
            raise result
        content = [TextBlock(text=json.dumps(result))] if isinstance(result, dict) else result
        return LLMResponse(content=content, stop_reason="tool_use" if any(isinstance(b, ToolCall) for b in content)
                           else "end_turn", usage=Usage(input_tokens=1000, output_tokens=200),
                           provider=self.name, model=config.model, latency_ms=5)

    @staticmethod
    def _call(name: str, **arguments: Any) -> list[Any]:
        return [ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments)]

    # --- roles -------------------------------------------------------------------------------------

    def _product_manager(self, step, prompt, request):
        answered = "Answers from the human stakeholder" in prompt
        return {
            "summary": "Add a per-project statistics endpoint.", "goals": ["Expose item counts by status"],
            "work_items": [{"id": "T1", "title": "Stats endpoint", "description": "GET /projects/{id}/stats",
                            "owner": "developer"}],
            "acceptance_criteria": [
                {"id": "AC1", "description": "GET /projects/{id}/stats returns counts per status",
                 "verification": "automated_test"},
                {"id": "AC2", "description": "Unknown project returns 404", "verification": "automated_test"}],
            "ambiguities": [{"question": "Include archived items?", "why_it_matters": "Counts differ",
                             "assumption": "No archived state exists", "blocking": self.blocking_question and not answered}],
            "non_functional_requirements": ["Single query per request is fine for now"], "out_of_scope": ["Caching"],
        }

    def _software_architect(self, step, prompt, request):
        if self.fail_architect_once:
            self.fail_architect_once = False
            from app.llm.types import LLMError

            return LLMError("simulated provider outage (non-retryable)")
        if step == 0:
            return self._call("read_file", path="app/routers/projects.py")
        return self._call("submit", overview="Add a stats route next to the item routes.", detected_stack=["FastAPI"],
                          approach="Count items per status in the projects router.",
                          components=[{"name": "projects router", "responsibility": "HTTP API",
                                       "files": ["app/routers/projects.py"]}],
                          affected_files=[{"path": "app/routers/projects.py", "action": "modify",
                                           "purpose": "new endpoint"}],
                          data_model_changes=[], api_changes=["GET /projects/{id}/stats"], dependencies_to_add=[],
                          risks=[{"risk": "N+1 queries", "severity": "low", "mitigation": "single select"}],
                          test_strategy="pytest with the existing TestClient fixture",
                          implementation_steps=["Add route", "Add tests"], revision_notes="")

    def _developer(self, step, prompt, request):
        iteration = int(re.search(r"Implementation iteration (\d+)", prompt).group(1))
        ids = re.findall(r"\[message_id=([^\]]+)\]", prompt)
        if iteration == 1:
            if step == 0:
                return self._call("read_file", path="app/routers/projects.py")
            if step == 1:
                return self._call("edit_file", path="app/routers/projects.py",
                                  old_text='@router.patch("/items/{item_id}"',
                                  new_text=BUGGY_ENDPOINT + '@router.patch("/items/{item_id}"')
        elif step == 0:
            return self._call("edit_file", path="app/routers/projects.py", old_text='        counts["todo"] += 1',
                              new_text="        counts[item.status] += 1")
        return self._call("submit", summary=f"Iteration {iteration}: stats endpoint", files_changed=["app/routers/projects.py"],
                          decisions=["Reuse _project_or_404 for consistent 404s"],
                          feedback_responses=[{"message_id": i, "action": "fixed", "rationale": "Count by item.status"}
                                              for i in ids],
                          dependencies_added=[], notes_for_qa="Covers todo/in_progress/done")

    def _qa_engineer(self, step, prompt, request):
        if step == 0:
            return self._call("write_file", path="tests/test_stats.py", content=STATS_TESTS)
        if step == 1:
            return self._call("run_command", argv=["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                                   "tests/test_stats.py"])
        return self._call("submit", tests_added_or_changed=["tests/test_stats.py"], test_plan="API-level tests",
                          criteria_covered=["AC1", "AC2"])

    def _qa_analysis(self, step, prompt, request):
        failing = re.search(r'"failed": [1-9]', prompt) is not None
        return {
            "summary": "Stats endpoint miscounts" if failing else "All tests pass",
            "failures": [{"test": "test_stats_counts_by_status", "root_cause": "every item is counted as todo",
                          "suggested_fix": "increment counts[item.status]", "file": "app/routers/projects.py",
                          "is_test_defect": False}] if failing else [],
            "acceptance": [{"criterion_id": "AC1", "status": "failed" if failing else "verified",
                            "evidence": "test_stats_counts_by_status"},
                           {"criterion_id": "AC2", "status": "verified", "evidence": "test_stats_missing_project"}],
            "feedback_for_developer": [], "test_quality_concerns": [],
        }

    def _security_engineer(self, step, prompt, request):
        return self._call("submit", summary="No exploitable issues in the new read-only endpoint.", findings=[],
                          tool_assessments=[], dispute_resolutions=[],
                          checked_areas=["authorization", "input_validation", "injection"])

    def _code_reviewer(self, step, prompt, request):
        return self._call("submit", verdict="approve", summary="Small, consistent change with tests.",
                          comments=[{"file": "app/routers/projects.py", "line": 70, "severity": "nit",
                                     "category": "style", "body": "Consider a response model."}],
                          architecture_consistency="Matches the design", strengths=["Tests cover both criteria"])

    def _deployment_engineer(self, step, prompt, request):
        return self._call("submit", summary="No infrastructure changes.", environment_variables=[],
                          infrastructure_requirements=[], docker_issues=[], migration_steps=[],
                          deployment_instructions=["Deploy as usual"], rollback_plan="Revert the commit",
                          production_readiness_risks=[])

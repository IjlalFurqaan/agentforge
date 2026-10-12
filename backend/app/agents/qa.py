from __future__ import annotations

from typing import Any

from app.agents.base import as_json, clip_diff, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import QAAnalysis, QAWork
from app.agents.test_results import TestRunResult, parse_junit
from app.agents.tools import exec_tool, read_tools, write_tools
from app.llm.types import Message
from app.sandbox import PolicyViolation, SandboxUnavailable

NAME = "qa"


async def write_tests(rt: AgentRuntime, *, requirements: dict[str, Any], design: dict[str, Any],
                      implementation: dict[str, Any], diff: str, environment: dict[str, Any]) -> QAWork:
    prompt = (
        "Write or extend automated tests that verify the acceptance criteria for this change, run them, "
        "then call submit."
        + section("Acceptance criteria and requirements", as_json(requirements, 10_000))
        + section("Test strategy from the design", design.get("test_strategy", ""))
        + section("Developer's implementation summary", as_json(implementation, 5000))
        + section("Sandbox environment", as_json(environment, 3000))
        + section("Diff under test", clip_diff(diff, 40_000))
    )
    tools = [*read_tools(rt), *write_tools(rt, test_only=True), exec_tool(rt, NAME)]
    return await run_tool_loop(rt, agent=NAME, system=system_prompt("qa"), prompt=prompt, tools=tools, output=QAWork,
                               max_tool_calls=min(rt.limits.max_tool_calls, 30),
                               submit_description="Submit the summary of the tests you wrote.")


async def run_test_suite(rt: AgentRuntime, test_command: list[str] | None, *, junit: bool) -> TestRunResult:
    """Execute the project's test suite in the sandbox. This — not the model — decides pass/fail."""
    if not test_command:
        return TestRunResult(executed=False, command=[], exit_code=None, timed_out=False, source="not_run",
                             reason="No test command could be inferred for this repository")
    report = rt.workspace.scratch / "junit.xml"
    report.unlink(missing_ok=True)
    try:
        result = await rt.execute(test_command, agent=NAME, purpose="test")
    except (PolicyViolation, SandboxUnavailable) as exc:
        return TestRunResult(executed=False, command=test_command, exit_code=None, timed_out=False,
                             source="not_run", reason=f"Sandbox could not run the tests: {exc}")
    run = TestRunResult(executed=True, command=test_command, exit_code=result.exit_code, timed_out=result.timed_out,
                        output_tail=(result.stdout[-6000:] + "\n" + result.stderr[-3000:]).strip(),
                        source="exit_code")
    if junit and report.exists():
        run = parse_junit(report, run)
    elif junit:
        run.source = "missing_report"
        run.reason = "Test runner did not produce a JUnit report (collection error, crash or disabled reporting)"
    return run


async def analyse(rt: AgentRuntime, *, requirements: dict[str, Any], run: TestRunResult, qa_work: dict[str, Any],
                  diff: str) -> QAAnalysis:
    prompt = (
        "Analyse the actual test results and verify each acceptance criterion."
        + section("Test execution (ground truth from the sandbox)", as_json({
            k: v for k, v in run.to_dict().items() if k != "output_tail"}, 15_000))
        + section("Test output tail", run.output_tail[-6000:])
        + section("Acceptance criteria", as_json(requirements.get("acceptance_criteria", []), 6000))
        + section("Tests written by QA", as_json(qa_work, 3000))
        + section("Diff", clip_diff(diff, 30_000))
    )
    return await rt.llm.structured(NAME, system_prompt("qa_analysis"), [Message.user(prompt)], QAAnalysis)

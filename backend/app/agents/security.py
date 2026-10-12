from __future__ import annotations

from typing import Any

from app.agents.base import as_json, clip_diff, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import SecurityReview
from app.agents.tools import read_tools
from app.security_tools.scanners import ScanSummary

NAME = "security"
BLOCKING = {"critical", "high"}


async def review(rt: AgentRuntime, *, requirements: dict[str, Any], design: dict[str, Any], diff: str,
                 scans: ScanSummary, disputes: list[dict[str, Any]],
                 previous_findings: list[dict[str, Any]]) -> SecurityReview:
    prompt = (
        "Review this change for security vulnerabilities. Inspect code with the tools where needed, "
        "then call submit."
        + section("Requirements (security-relevant parts matter most)", as_json(requirements, 8000))
        + section("Design risks identified by the architect", as_json(design.get("risks", []), 4000))
        + section("Scanner findings (deterministic tools)", as_json([f.to_dict() for f in scans.findings], 12_000))
        + section("Scanners skipped", as_json(scans.tools_skipped))
        + section("Diff", clip_diff(diff, 50_000))
    )
    if previous_findings:
        prompt += section("Your findings from the previous review round", as_json(previous_findings, 8000))
    if disputes:
        prompt += section("Developer disputes to resolve (by message_id)", "\n\n".join(
            f"[message_id={d['id']}] {d['content']}" for d in disputes))
    return await run_tool_loop(rt, agent=NAME, system=system_prompt(NAME), prompt=prompt, tools=read_tools(rt),
                               output=SecurityReview, max_tool_calls=min(rt.limits.max_tool_calls, 15),
                               submit_description="Submit the security review.")


def blocking_issues(scans: ScanSummary, review_out: SecurityReview, withdrawn: set[str]) -> list[dict[str, Any]]:
    """Issues that must be fixed before the PR gate. Tool findings stay blocking unless the reviewer
    explicitly marked them false positives (with a rationale that is shown to humans). `withdrawn`
    holds issue ids the security agent conceded after a developer dispute."""
    false_positive = {a.finding_id for a in review_out.tool_assessments if a.verdict == "false_positive"}
    issues: list[dict[str, Any]] = []
    for f in scans.findings:
        strong = f.severity in BLOCKING and (f.tool != "bandit" or f.confidence in {"high", "medium"})
        if strong and f.id not in false_positive and f"tool-{f.id}" not in withdrawn:
            issues.append({"id": f"tool-{f.id}", "source": f.tool, "severity": f.severity, "title": f.rule,
                           "file": f.file, "line": f.line, "detail": f.message, "remediation": ""})
    for s in review_out.findings:
        if s.severity in BLOCKING and f"sec-{s.id}" not in withdrawn:
            issues.append({"id": f"sec-{s.id}", "source": "security-agent", "severity": s.severity, "title": s.title,
                           "file": s.file, "line": s.line, "detail": s.description, "remediation": s.remediation})
    return issues

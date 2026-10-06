"""Deterministic security scanners executed inside the sandbox."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.agents.runtime import AgentRuntime
from app.sandbox import PolicyViolation, SandboxUnavailable
from app.security_tools.secrets import scan_diff

RULES_FILE = Path(__file__).parent / "rules" / "agentforge.yml"
BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
SEMGREP_SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


@dataclass
class ToolFinding:
    id: str
    tool: str
    rule: str
    severity: str
    confidence: str
    file: str
    line: int
    message: str
    cwe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanSummary:
    findings: list[ToolFinding]
    tools_run: list[str]
    tools_skipped: dict[str, str]


async def run_bandit(rt: AgentRuntime, files: list[str]) -> tuple[list[ToolFinding], str | None]:
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return [], "no Python files changed"
    try:
        result = await rt.execute(["bandit", "-f", "json", "-q", *py[:50]], agent="security", purpose="scan")
    except (PolicyViolation, SandboxUnavailable) as exc:
        return [], str(exc)
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return [], f"bandit produced no JSON (exit {result.exit_code}): {result.stderr[-300:]}"
    findings = []
    for i, r in enumerate(data.get("results", []), 1):
        findings.append(ToolFinding(
            id=f"B{i}", tool="bandit", rule=f"{r.get('test_id')}:{r.get('test_name')}",
            severity=BANDIT_SEVERITY.get(r.get("issue_severity", "LOW"), "low"),
            confidence=str(r.get("issue_confidence", "")).lower(), file=r.get("filename", "").lstrip("./"),
            line=int(r.get("line_number") or 0), message=r.get("issue_text", ""),
            cwe=f"CWE-{r['issue_cwe']['id']}" if isinstance(r.get("issue_cwe"), dict) else ""))
    return findings, None


async def run_semgrep(rt: AgentRuntime, files: list[str]) -> tuple[list[ToolFinding], str | None]:
    if not files:
        return [], "no files changed"
    if rt.sandbox.name == "seatbelt" and shutil.which("semgrep") is None:
        return [], "semgrep is not installed on this host (it is included in the Docker sandbox image)"
    rules_copy = rt.workspace.scratch / "semgrep-rules.yml"
    rules_copy.write_text(RULES_FILE.read_text())
    try:
        result = await rt.execute(["semgrep", "--config", ".agentforge/semgrep-rules.yml", "--json", "--quiet",
                                   "--metrics=off", "--disable-version-check", *files[:100]],
                                  agent="security", purpose="scan")
    except (PolicyViolation, SandboxUnavailable) as exc:
        return [], str(exc)
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return [], f"semgrep produced no JSON (exit {result.exit_code}): {result.stderr[-300:]}"
    findings = []
    for i, r in enumerate(data.get("results", []), 1):
        extra = r.get("extra", {})
        meta = extra.get("metadata", {}) or {}
        findings.append(ToolFinding(
            id=f"G{i}", tool="semgrep", rule=r.get("check_id", "").rsplit(".", 1)[-1],
            severity=SEMGREP_SEVERITY.get(extra.get("severity", "INFO"), "low"), confidence="medium",
            file=r.get("path", ""), line=int(r.get("start", {}).get("line") or 0), message=extra.get("message", ""),
            cwe=str(meta.get("cwe", ""))))
    return findings, None


async def run_scanners(rt: AgentRuntime, changed_files: list[str], diff_text: str) -> ScanSummary:
    findings: list[ToolFinding] = []
    ran: list[str] = []
    skipped: dict[str, str] = {}
    for i, s in enumerate(scan_diff(diff_text), 1):
        findings.append(ToolFinding(id=f"K{i}", tool="secret-scanner", rule=s.rule, severity=s.severity,
                                    confidence="high", file=s.file, line=s.line, message=f"Possible secret: {s.excerpt}",
                                    cwe="CWE-798"))
    ran.append("secret-scanner")
    existing = [f for f in changed_files if (rt.workspace.repo / f).exists()]
    for name, runner in (("bandit", run_bandit), ("semgrep", run_semgrep)):
        tool_findings, reason = await runner(rt, existing)
        if reason:
            skipped[name] = reason
        else:
            ran.append(name)
            findings.extend(tool_findings)
    return ScanSummary(findings=findings, tools_run=ran, tools_skipped=skipped)

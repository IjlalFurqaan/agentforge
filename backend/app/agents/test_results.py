"""JUnit XML parsing: the single source of truth for test outcomes."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 - only for the ParseError type
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import parse as safe_parse

# The report is written by agent-authored test code, so treat it as hostile input.
MAX_REPORT_BYTES = 20_000_000


@dataclass
class TestCaseFailure:
    __test__ = False  # not a pytest test class
    name: str
    classname: str
    kind: str  # failure | error
    message: str
    details: str


@dataclass
class TestRunResult:
    __test__ = False
    executed: bool
    command: list[str]
    exit_code: int | None
    timed_out: bool
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failures: list[TestCaseFailure] = field(default_factory=list)
    source: str = "junit"  # junit | exit_code | invalid_report | missing_report | not_run
    output_tail: str = ""
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        if not self.executed or self.timed_out or self.source in {"invalid_report", "missing_report"}:
            return False  # a JUnit report was expected: never fall back to trusting the exit code alone
        if self.source == "junit":
            return self.exit_code == 0 and self.failed == 0 and self.errors == 0 and self.total > 0
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["succeeded"] = self.succeeded
        return data


def parse_junit(path: Path, run: TestRunResult) -> TestRunResult:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError(f"report larger than {MAX_REPORT_BYTES} bytes")
        root = safe_parse(path).getroot()
    except (ET.ParseError, DefusedXmlException, OSError, ValueError) as exc:
        run.source = "invalid_report"
        run.reason = f"JUnit report unreadable or unsafe: {exc}"
        return run
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for case in suite.iter("testcase"):
            run.total += 1
            run.duration_s += float(case.get("time") or 0)
            failure = case.find("failure")
            error = case.find("error")
            if case.find("skipped") is not None:
                run.skipped += 1
            elif failure is not None or error is not None:
                node = failure if failure is not None else error
                kind = "failure" if failure is not None else "error"
                if kind == "failure":
                    run.failed += 1
                else:
                    run.errors += 1
                run.failures.append(TestCaseFailure(
                    name=case.get("name", "?"), classname=case.get("classname", ""), kind=kind,
                    message=(node.get("message") or "")[:500], details=(node.text or "")[-3000:]))
            else:
                run.passed += 1
    run.duration_s = round(run.duration_s, 3)
    run.source = "junit"
    return run

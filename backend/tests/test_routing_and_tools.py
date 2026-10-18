from __future__ import annotations

import shutil

import pytest
from langgraph.graph import END

from app.agents.runtime import WorkflowLimits
from app.agents.test_results import TestRunResult, parse_junit
from app.orchestration import routing
from app.orchestration.nodes import _added_lines, review_comments
from app.security_tools.secrets import scan_diff
from app.vcs import git_ops

LIMITS = WorkflowLimits(max_dev_iterations=3, max_arch_revisions=1, max_tool_calls=20, max_cost_usd=1, max_wall_clock_minutes=5)


def test_routing_after_qa():
    failing = {"qa": {"passed": False, "run": {"executed": True, "failed": 2, "errors": 0}}}
    assert routing.after_qa({**failing, "dev_iterations": 1}, LIMITS)[0] == "developer"
    assert routing.after_qa({**failing, "dev_iterations": 3}, LIMITS)[0] == "security"  # iteration limit
    assert routing.after_qa({"qa": {"passed": True, "run": {"executed": True}}}, LIMITS)[0] == "security"
    assert routing.after_qa({"qa": {"passed": False, "run": {"executed": False, "reason": "x"}}}, LIMITS)[0] == "security"


def test_routing_after_security_and_review():
    blocked = {"security": {"blocking": [{"id": "sec-S1"}]}, "dev_iterations": 1}
    assert routing.after_security(blocked, LIMITS)[0] == "developer"
    assert routing.after_security({**blocked, "dev_iterations": 3}, LIMITS)[0] == "reviewer"
    assert routing.after_review({"review": {"verdict": "request_redesign"}, "arch_revisions": 0}, LIMITS)[0] == "architect"
    assert routing.after_review({"review": {"verdict": "request_redesign"}, "arch_revisions": 1,
                                 "dev_iterations": 1}, LIMITS)[0] == "developer"
    assert routing.after_review({"review": {"verdict": "request_changes"}, "dev_iterations": 3}, LIMITS)[0] == "deployment"
    assert routing.after_review({"review": {"verdict": "approve"}}, LIMITS)[0] == "deployment"


def test_routing_gates():
    blocking_req = {"requirements": {"ambiguities": [{"blocking": True}]}}
    assert routing.after_product_manager(blocking_req)[0] == "clarify"
    assert routing.after_product_manager({**blocking_req, "clarification_rounds": 1})[0] == "architect"
    assert routing.after_product_manager({**blocking_req, "config": {"approvals": {"clarifications": False}}})[0] == "architect"
    assert routing.after_architect({"config": {"approvals": {"plan": True}}})[0] == "plan_review"
    assert routing.after_plan_review({"plan_approved": False, "arch_revisions": 1}, LIMITS)[0] == END
    assert routing.after_pr_review({"pr": {"approved": True}})[0] == "publish_pr"
    assert routing.after_pr_review({"config": {"approvals": {"pull_request": "skip"}}})[0] == END


def test_junit_parsing(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest" tests="4">'
        '<testcase classname="tests.test_a" name="test_ok" time="0.1"/>'
        '<testcase classname="tests.test_a" name="test_bad" time="0.2"><failure message="assert 1 == 2">trace</failure></testcase>'
        '<testcase classname="tests.test_a" name="test_err"><error message="boom"/></testcase>'
        '<testcase classname="tests.test_a" name="test_skip"><skipped/></testcase>'
        "</testsuite></testsuites>")
    run = parse_junit(report, TestRunResult(executed=True, command=["pytest"], exit_code=1, timed_out=False))
    assert (run.total, run.passed, run.failed, run.errors, run.skipped) == (4, 1, 1, 1, 1)
    assert not run.succeeded and run.failures[0].message == "assert 1 == 2"
    # A zero exit code with zero collected tests is not a success
    empty = TestRunResult(executed=True, command=["pytest"], exit_code=0, timed_out=False, source="junit")
    assert not empty.succeeded


def test_junit_parser_rejects_hostile_reports(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
                      '<testsuite><testcase name="&b;"/></testsuite>')
    run = parse_junit(report, TestRunResult(executed=True, command=["pytest"], exit_code=0, timed_out=False))
    assert run.source == "invalid_report" and not run.succeeded  # exit code 0 alone is not trusted


DIFF = """diff --git a/app/config.py b/app/config.py
--- a/app/config.py
+++ b/app/config.py
@@ -1,2 +1,4 @@
 x = 1
+AWS_KEY = "AKIAABCDEFGHIJKLMNOP"
+jwt_secret = "Zq8#kL2!vN9@pR4$wT7%"
+password = "changeme-placeholder"
diff --git a/tests/test_x.py b/tests/test_x.py
--- /dev/null
+++ b/tests/test_x.py
@@ -0,0 +1,1 @@
+token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
"""


def test_secret_scanner():
    findings = scan_diff(DIFF)
    rules = {(f.rule, f.file, f.line, f.severity) for f in findings}
    assert ("aws-access-key", "app/config.py", 2, "critical") in rules
    assert ("hardcoded-credential", "app/config.py", 3, "high") in rules
    assert not any(f.line == 4 and f.file == "app/config.py" for f in findings)  # placeholder ignored
    assert ("github-token", "tests/test_x.py", 1, "medium") in rules  # downgraded in tests
    assert all("AKIAABCDEFGHIJKLMNOP" not in f.excerpt for f in findings)


def test_review_comment_mapping():
    added = _added_lines(DIFF)
    assert added["app/config.py"] == {2, 3, 4}
    inline, leftover = review_comments({"comments": [
        {"file": "app/config.py", "line": 3, "severity": "major", "category": "bug", "body": "hard-coded"},
        {"file": "app/config.py", "line": 1, "severity": "minor", "category": "style", "body": "unchanged line"},
    ]}, DIFF)
    assert len(inline) == 1 and inline[0]["line"] == 3 and len(leftover) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_git_operations(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("print(1)\n")
    await git_ops.init_from_directory(src, tmp_path / "repo", ("Bot", "bot@example.com"))
    repo = tmp_path / "repo"
    base = await git_ops.head_commit(repo)
    await git_ops.create_branch(repo, "agentforge/test-branch")
    (repo / "a.py").write_text("print(2)\n")
    (repo / "b.py").write_text("x = 1\n")
    (repo / ".agentforge").mkdir()
    (repo / ".agentforge" / "junit.xml").write_text("<x/>")
    changed = {f.path: f.status for f in await git_ops.changed_files(repo, base)}
    assert changed == {"a.py": "M", "b.py": "A"}  # platform files are excluded
    assert "+print(2)" in await git_ops.diff(repo, base)
    sha = await git_ops.commit(repo, "change", ("Bot", "bot@example.com"))
    assert sha != base
    with pytest.raises(git_ops.GitError):
        await git_ops.push(repo, "main", token=None, protected={"main"})
    with pytest.raises(git_ops.GitError):
        await git_ops.create_branch(repo, "bad..name")

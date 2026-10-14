"""LangGraph nodes: each wraps one agent (or platform step), updates state and records the audit trail."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END
from langgraph.types import interrupt
from sqlalchemy import update

from app.agents import architect, deployment, developer, product_manager, qa, reviewer, security
from app.agents.base import open_feedback
from app.agents.runtime import AgentRuntime
from app.db.models import Task
from app.db.session import session_scope
from app.intelligence.analyzer import analyze_repository
from app.orchestration import routing
from app.orchestration.environment import ensure_environment
from app.security_tools.scanners import run_scanners
from app.vcs import git_ops
from app.vcs.github import GitHubClient, GitHubError


@dataclass
class TaskContext:
    task_id: uuid.UUID
    title: str
    repo_source: str
    repo_locator: str
    default_branch: str
    base_commit: str
    branch_name: str
    github_token: str | None
    author: tuple[str, str]
    github_api_url: str = "https://api.github.com"


def _route(node: str, decision: routing.Route) -> dict[str, Any]:
    target, reason = decision
    entry = {"from": node, "to": target, "reason": reason}
    return {"route": entry, "route_log": [entry]}


class WorkflowNodes:
    def __init__(self, rt: AgentRuntime, ctx: TaskContext) -> None:
        self.rt = rt
        self.ctx = ctx

    # --- helpers ------------------------------------------------------------------------------

    async def _decide(self, node: str, decision: routing.Route) -> dict[str, Any]:
        target, reason = decision
        await self.rt.emit("route", f"{node} → {'END' if target == END else target}: {reason}",
                           payload={"from": node, "to": target, "reason": reason})
        return _route(node, decision)

    async def _diff(self) -> tuple[str, list[dict[str, Any]], str]:
        repo = self.rt.workspace.repo
        diff = await git_ops.diff(repo, self.ctx.base_commit)
        files = [vars(f) for f in await git_ops.changed_files(repo, self.ctx.base_commit)]
        stat = "\n".join(f"{f['status']} {f['path']} (+{f['additions']}/-{f['deletions']})" for f in files)
        return diff, files, stat

    # --- nodes --------------------------------------------------------------------------------

    async def analyze_repo(self, state: dict[str, Any]) -> dict[str, Any]:
        s = self.rt.settings
        analysis = await asyncio.to_thread(analyze_repository, self.rt.workspace.repo, max_files=s.max_repo_files,
                                           max_file_bytes=s.max_file_bytes)
        data = analysis.model_dump()
        await self.rt.events.save_artifact("repo_analysis", data, agent="architect")
        await self.rt.emit("analysis", f"Static analysis: {analysis.file_count} files, primary language "
                           f"{analysis.primary_language}, {len(analysis.frameworks)} frameworks, "
                           f"{analysis.testing.test_files} test files", agent="architect",
                           payload={"frameworks": data["frameworks"], "testing": data["testing"],
                                    "observations": data["observations"]})
        return {"repo_brief": analysis.brief(), "readme": analysis.readme_excerpt, "testing": data["testing"],
                "infrastructure": data["infrastructure"], "dependencies": data["dependencies"],
                "dev_iterations": 0, "arch_revisions": 0, "clarification_rounds": 0, "design_version": 0}

    async def product_manager(self, state: dict[str, Any]) -> dict[str, Any]:
        spec = await product_manager.run(
            self.rt, request=state["request"], repo_brief=state["repo_brief"], readme=state.get("readme"),
            clarifications=state.get("clarifications", []),
            previous=state.get("requirements") if state.get("clarifications") else None)
        data = spec.model_dump()
        await self.rt.events.save_artifact("requirements", data, agent=product_manager.NAME)
        blocking = sum(a.blocking for a in spec.ambiguities)
        msg = await self.rt.message(
            product_manager.NAME, "architect", "handoff",
            f"Requirements ready: {len(spec.work_items)} work items, {len(spec.acceptance_criteria)} acceptance "
            f"criteria, {len(spec.ambiguities)} ambiguities ({blocking} blocking). Assumptions: "
            + "; ".join(a.assumption for a in spec.ambiguities[:5]))
        update_ = {"requirements": data, "messages": [msg]}
        return {**update_, **await self._decide("product_manager", routing.after_product_manager({**state, **update_}))}

    async def clarify(self, state: dict[str, Any]) -> dict[str, Any]:
        questions = [a for a in state["requirements"]["ambiguities"] if a.get("blocking")]
        answer = interrupt({"kind": "clarification", "title": "The Product Manager needs answers", "questions": questions})
        answers = (answer or {}).get("answers") or []
        return {"clarifications": [*state.get("clarifications", []), *answers],
                "clarification_rounds": state.get("clarification_rounds", 0) + 1}

    async def architect(self, state: dict[str, Any]) -> dict[str, Any]:
        revision = bool(state.get("design"))
        design = await architect.run(self.rt, request=state["request"], requirements=state["requirements"],
                                     repo_brief=state["repo_brief"], previous_design=state.get("design"),
                                     feedback=state.get("design_feedback", []))
        data = design.model_dump()
        version = state.get("design_version", 0) + 1
        await self.rt.events.save_artifact("design", {**data, "version": version}, agent=architect.NAME)
        files = ", ".join(f"{f.action} {f.path}" for f in design.affected_files[:12])
        msg = await self.rt.message(architect.NAME, "developer", "handoff",
                                    f"Design v{version} ready. Affected files: {files}. Risks: "
                                    + "; ".join(r.risk for r in design.risks[:4]))
        resolved = [m["id"] for m in open_feedback(state.get("messages", []), state.get("resolved_message_ids", []),
                                                   "architect")]
        update_ = {"design": data, "design_version": version, "design_feedback": [], "messages": [msg],
                   "resolved_message_ids": resolved,
                   "arch_revisions": state.get("arch_revisions", 0) + (1 if revision else 0)}
        return {**update_, **await self._decide("architect", routing.after_architect({**state, **update_}))}

    async def plan_review(self, state: dict[str, Any]) -> dict[str, Any]:
        decision = interrupt({"kind": "plan", "title": f"Approve design v{state.get('design_version', 1)}",
                              "requirements": state["requirements"], "design": state["design"]})
        approved = bool((decision or {}).get("approved"))
        comment = (decision or {}).get("comment") or ""
        update_: dict[str, Any] = {"plan_approved": approved}
        if not approved:
            update_["design_feedback"] = [f"Human reviewer rejected the plan: {comment or 'no reason given'}"]
        return {**update_, **await self._decide("plan_review", routing.after_plan_review({**state, **update_}, self.rt.limits))}

    async def setup_env(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"environment": await ensure_environment(self.rt, state)}

    async def developer(self, state: dict[str, Any]) -> dict[str, Any]:
        iteration = state.get("dev_iterations", 0) + 1
        self.rt.events.iteration = iteration
        feedback = open_feedback(state.get("messages", []), state.get("resolved_message_ids", []), "developer")
        summary = await developer.run(
            self.rt, request=state["request"], requirements=state["requirements"], design=state["design"],
            feedback=feedback, previous=state.get("implementation"), diff_stat=state.get("diff_stat", ""),
            environment=state.get("environment", {}))
        diff, files, stat = await self._diff()
        data = summary.model_dump()
        await self.rt.events.save_artifact("implementation", data, agent=developer.NAME)
        await self.rt.events.save_artifact("diff", {"diff": diff, "files": files, "stat": stat}, agent=developer.NAME)
        by_id = {m["id"]: m for m in feedback}
        messages = []
        for r in summary.feedback_responses:
            original = by_id.get(r.message_id)
            if original is None:
                continue
            kind = "dispute" if r.action == "disputed" else "response"
            messages.append(await self.rt.message(
                developer.NAME, original["from"], kind, f"{r.action.upper()}: {r.rationale}",
                refs={"original": r.message_id, "issue_id": original.get("refs", {}).get("issue_id")}))
        if not files:
            await self.rt.emit("warning", "Developer finished without changing any files", agent=developer.NAME)
        return {"implementation": data, "dev_iterations": iteration, "changed_files": files, "diff_stat": stat,
                "messages": messages, "resolved_message_ids": [m["id"] for m in feedback]}

    async def qa(self, state: dict[str, Any]) -> dict[str, Any]:
        self.rt.events.iteration = state.get("dev_iterations", 0)
        env = await ensure_environment(self.rt, state)
        diff, _, _ = await self._diff()
        work = await qa.write_tests(self.rt, requirements=state["requirements"], design=state["design"],
                                    implementation=state.get("implementation", {}), diff=diff, environment=env)
        run = await qa.run_test_suite(self.rt, env.get("test_command"), junit=env.get("junit", False))
        await self.rt.emit("test_results", (
            f"Tests: {run.passed} passed, {run.failed} failed, {run.errors} errors, {run.skipped} skipped "
            f"(source: {run.source})" if run.executed else f"Tests not executed: {run.reason}"), agent=qa.NAME,
            payload={k: v for k, v in run.to_dict().items() if k != "output_tail"})
        diff, files, stat = await self._diff()
        analysis = None
        if run.executed:
            analysis = (await qa.analyse(self.rt, requirements=state["requirements"], run=run,
                                         qa_work=work.model_dump(), diff=diff)).model_dump()
        passed = run.succeeded
        messages = []
        if run.executed and not passed and analysis:
            for f in [f for f in analysis["failures"] if not f["is_test_defect"]][:8]:
                messages.append(await self.rt.message(
                    qa.NAME, "developer", "feedback",
                    f"Test `{f['test']}` fails. Root cause: {f['root_cause']} Suggested fix ({f['file']}): {f['suggested_fix']}"))
            if analysis["feedback_for_developer"]:
                messages.append(await self.rt.message(qa.NAME, "developer", "feedback",
                                                      "\n".join(f"- {x}" for x in analysis["feedback_for_developer"][:10])))
            if not messages:
                messages.append(await self.rt.message(
                    qa.NAME, "developer", "feedback",
                    f"Test suite failed (exit {run.exit_code}). Output tail:\n{run.output_tail[-2000:]}"))
        report = {"passed": passed, "run": run.to_dict(), "work": work.model_dump(), "analysis": analysis}
        await self.rt.events.save_artifact("test_report", report, agent=qa.NAME)
        update_ = {"qa": report, "environment": env, "messages": messages, "changed_files": files, "diff_stat": stat}
        return {**update_, **await self._decide("qa", routing.after_qa({**state, **update_}, self.rt.limits))}

    async def security(self, state: dict[str, Any]) -> dict[str, Any]:
        diff, files, _ = await self._diff()
        scans = await run_scanners(self.rt, [f["path"] for f in files if f["status"] != "D"], diff)
        await self.rt.emit("scan_results", f"Scanners: {', '.join(scans.tools_run)}; {len(scans.findings)} raw findings"
                           + (f"; skipped: {', '.join(scans.tools_skipped)}" if scans.tools_skipped else ""),
                           agent=security.NAME, payload={"findings": [f.to_dict() for f in scans.findings],
                                                         "skipped": scans.tools_skipped})
        disputes = [m for m in open_feedback(state.get("messages", []), state.get("resolved_message_ids", []), "security")
                    if m["kind"] == "dispute"]
        previous = (state.get("security") or {}).get("review", {}).get("findings", [])
        review = await security.review(self.rt, requirements=state["requirements"], design=state["design"], diff=diff,
                                       scans=scans, disputes=disputes, previous_findings=previous)
        resolution = {d.message_id: d.verdict for d in review.dispute_resolutions}
        withdrawn = set(state.get("security_withdrawn", []))
        for d in disputes:
            if resolution.get(d["id"]) == "withdrawn" and d.get("refs", {}).get("issue_id"):
                withdrawn.add(d["refs"]["issue_id"])
        blocking = security.blocking_issues(scans, review, withdrawn)
        iteration = state.get("dev_iterations", 0)
        messages = []
        for issue in blocking[:10]:
            messages.append(await self.rt.message(
                security.NAME, "developer", "finding",
                f"[{issue['severity'].upper()}] {issue['title']} at {issue['file']}:{issue['line']} — {issue['detail']}"
                + (f" Remediation: {issue['remediation']}" if issue["remediation"] else ""),
                refs={"issue_id": issue["id"]}, message_id=f"{issue['id']}-i{iteration}"))
        for d in disputes:
            messages.append(await self.rt.message(
                security.NAME, "developer", "response",
                f"Dispute {d['id']}: {resolution.get(d['id'], 'not addressed')}", refs={"original": d["id"]}))
        report = {"tool_findings": [f.to_dict() for f in scans.findings], "tools_run": scans.tools_run,
                  "tools_skipped": scans.tools_skipped, "review": review.model_dump(), "blocking": blocking}
        await self.rt.events.save_artifact("security_report", report, agent=security.NAME)
        update_ = {"security": report, "security_withdrawn": sorted(withdrawn), "messages": messages,
                   "resolved_message_ids": [d["id"] for d in disputes]}
        return {**update_, **await self._decide("security", routing.after_security({**state, **update_}, self.rt.limits))}

    async def reviewer(self, state: dict[str, Any]) -> dict[str, Any]:
        diff, _, _ = await self._diff()
        qa_state = state.get("qa", {})
        tests = {k: v for k, v in qa_state.get("run", {}).items() if k != "output_tail"}
        sec = state.get("security", {})
        result = await reviewer.run(self.rt, requirements=state["requirements"], design=state["design"],
                                    implementation=state.get("implementation", {}), diff=diff, tests=tests,
                                    security={"blocking": sec.get("blocking", []),
                                              "summary": sec.get("review", {}).get("summary")})
        data = result.model_dump()
        await self.rt.events.save_artifact("review", data, agent=reviewer.NAME)
        messages: list[dict[str, Any]] = []
        update_: dict[str, Any] = {"review": data}
        serious = [c for c in result.comments if c.severity in {"blocker", "major"}]
        if result.verdict == "request_changes" or (result.verdict == "request_redesign" and serious):
            for c in serious[:10]:
                messages.append(await self.rt.message(reviewer.NAME, "developer", "change_request",
                                                      f"[{c.severity}] {c.file}:{c.line} ({c.category}) {c.body}"))
        if result.verdict == "request_redesign":
            messages.append(await self.rt.message(reviewer.NAME, "architect", "redesign_request", result.summary))
            update_["design_feedback"] = [f"Code reviewer requested a redesign: {result.summary}"] + [
                f"{c.file}:{c.line} {c.body}" for c in serious[:8]]
        update_["messages"] = messages
        return {**update_, **await self._decide("reviewer", routing.after_review({**state, **update_}, self.rt.limits))}

    async def deployment(self, state: dict[str, Any]) -> dict[str, Any]:
        diff, _, _ = await self._diff()
        checks = deployment.static_checks(self.rt, diff, state.get("infrastructure", {}))
        result = await deployment.run(self.rt, requirements=state["requirements"], design=state["design"],
                                      diff=diff, checks=checks)
        data = {"checks": checks, "review": result.model_dump()}
        await self.rt.events.save_artifact("deployment", data, agent=deployment.NAME)
        return {"deployment": data}

    async def final_report(self, state: dict[str, Any]) -> dict[str, Any]:
        repo = self.rt.workspace.repo
        _, files, stat = await self._diff()
        commit = None
        if files:
            body = f"AgentForge: {self.ctx.title}\n\nAutomated change for task {self.ctx.task_id}.\n\n{stat}\n"
            commit = await git_ops.commit(repo, body, self.ctx.author)
        report = build_report(state, files=files, commit=commit, ctx=self.ctx, sandbox=self.rt.sandbox.name)
        await self.rt.events.save_artifact("final_report", report, agent=None)
        await self.rt.emit("report", f"Final report: {report['status']} — {len(files)} files changed, "
                           f"{len(report['limitations'])} limitation(s) recorded", payload={"status": report["status"]})
        return {"report": report, "changed_files": files, "diff_stat": stat}

    async def pr_review(self, state: dict[str, Any]) -> dict[str, Any]:
        if routing.approvals(state)["pull_request"] == "skip":
            return await self._decide("pr_review", routing.after_pr_review(state))
        report = state["report"]
        decision = interrupt({"kind": "pull_request", "title": f"Open a pull request for '{self.ctx.title}'?",
                              "branch": self.ctx.branch_name, "base": self.ctx.default_branch,
                              "status": report["status"], "files": report["files_modified"],
                              "limitations": report["limitations"], "can_push": self._can_push()[0]})
        approved = bool((decision or {}).get("approved"))
        update_ = {"pr": {"approved": approved, "comment": (decision or {}).get("comment") or ""}}
        return {**update_, **await self._decide("pr_review", routing.after_pr_review({**state, **update_}))}

    def _can_push(self) -> tuple[bool, str]:
        if self.ctx.repo_source != "github":
            return False, "repository is not connected through GitHub; the branch is prepared locally only"
        if not self.ctx.github_token:
            return False, "no GitHub token available for this user"
        return True, ""

    async def publish_pr(self, state: dict[str, Any]) -> dict[str, Any]:
        pr: dict[str, Any] = {**state.get("pr", {}), "branch": self.ctx.branch_name,
                              "commit": state["report"].get("commit")}
        can_push, reason = self._can_push()
        if not state["report"].get("commit"):
            can_push, reason = False, "no changes were committed"
        if not can_push:
            pr.update(status="not_pushed", reason=reason)
            await self.rt.emit("pull_request", f"Pull request not opened: {reason}", payload=pr)
            await self.rt.events.save_artifact("pull_request", pr)
            return {"pr": pr}
        full_name = self.ctx.repo_locator
        async with GitHubClient(self.ctx.github_token or "", self.ctx.github_api_url) as gh:
            protected = {self.ctx.default_branch}
            if await gh.branch_protected(full_name, self.ctx.branch_name):
                protected.add(self.ctx.branch_name)
            await git_ops.push(self.rt.workspace.repo, self.ctx.branch_name, token=self.ctx.github_token,
                               protected=protected)
            created = await gh.create_pull_request(full_name, head=self.ctx.branch_name, base=self.ctx.default_branch,
                                                   title=f"[AgentForge] {self.ctx.title}",
                                                   body=pr_body(state["report"]), draft=True)
            pr.update(status="opened", url=created.get("html_url"), number=created.get("number"))
            diff = await git_ops.diff(self.rt.workspace.repo, self.ctx.base_commit)
            comments, leftover = review_comments(state.get("review", {}), diff)
            try:
                await gh.create_review(full_name, created["number"], body=review_body(state.get("review", {}), leftover),
                                       comments=comments)
                pr["review_comments"] = len(comments)
            except GitHubError as exc:
                pr["review_error"] = str(exc)
        async with session_scope() as s:
            await s.execute(update(Task).where(Task.id == self.ctx.task_id).values(pr_url=pr.get("url")))
        await self.rt.emit("pull_request", f"Draft pull request opened: {pr.get('url')}", payload=pr)
        await self.rt.events.save_artifact("pull_request", pr)
        return {"pr": pr}


# --- reporting helpers ----------------------------------------------------------------------------


def build_report(state: dict[str, Any], *, files: list[dict[str, Any]], commit: str | None, ctx: TaskContext,
                 sandbox: str) -> dict[str, Any]:
    req = state.get("requirements", {})
    qa_state = state.get("qa", {})
    run = qa_state.get("run", {})
    analysis = qa_state.get("analysis") or {}
    verdicts = {v["criterion_id"]: v for v in analysis.get("acceptance", [])}
    acceptance = [{**c, "status": verdicts.get(c["id"], {}).get("status", "not_verified"),
                   "evidence": verdicts.get(c["id"], {}).get("evidence", "")} for c in req.get("acceptance_criteria", [])]
    sec = state.get("security", {})
    review = state.get("review", {})
    deploy = state.get("deployment", {})
    limitations: list[str] = []
    if not run.get("executed"):
        limitations.append(f"Automated tests were not executed: {run.get('reason')}")
    elif not qa_state.get("passed"):
        limitations.append(f"Test suite is failing: {run.get('failed', 0)} failed, {run.get('errors', 0)} errors")
    if sec.get("blocking"):
        limitations.append(f"{len(sec['blocking'])} blocking security issue(s) remain unresolved")
    for tool, why in (sec.get("tools_skipped") or {}).items():
        limitations.append(f"Security scanner '{tool}' did not run: {why}")
    if review.get("verdict") and review["verdict"] != "approve":
        limitations.append(f"Code review verdict is '{review['verdict']}'")
    unverified = [a["id"] for a in acceptance if a["status"] != "verified"]
    if unverified:
        limitations.append(f"Acceptance criteria not verified by evidence: {', '.join(unverified)}")
    if not files:
        limitations.append("No files were changed")
    if sandbox == "seatbelt":
        limitations.append("Executed with the development Seatbelt sandbox (not the production Docker sandbox)")
    if ctx.repo_source != "github":
        limitations.append("Repository is not connected through GitHub; a pull request cannot be opened automatically")
    for a in req.get("ambiguities", []):
        limitations.append(f"Assumption: {a['assumption']} (open question: {a['question']})")
    healthy = run.get("executed") and qa_state.get("passed") and not sec.get("blocking") and review.get("verdict") == "approve"
    return {
        "title": ctx.title,
        "request": state.get("request"),
        "status": "ready_for_review" if healthy else "needs_attention",
        "summary": req.get("summary"),
        "requirements": {"goals": req.get("goals", []), "acceptance": acceptance,
                         "work_items": req.get("work_items", [])},
        "design": {"version": state.get("design_version"), "overview": state.get("design", {}).get("overview"),
                   "risks": state.get("design", {}).get("risks", [])},
        "implementation": {"summary": state.get("implementation", {}).get("summary"),
                           "decisions": state.get("implementation", {}).get("decisions", [])},
        "files_modified": files,
        "tests": {k: v for k, v in run.items() if k != "output_tail"},
        "tests_written": qa_state.get("work", {}).get("tests_added_or_changed", []),
        "security": {"tools_run": sec.get("tools_run", []), "tools_skipped": sec.get("tools_skipped", {}),
                     "tool_findings": len(sec.get("tool_findings", [])), "blocking": sec.get("blocking", []),
                     "findings": sec.get("review", {}).get("findings", []),
                     "summary": sec.get("review", {}).get("summary")},
        "review": {"verdict": review.get("verdict"), "summary": review.get("summary"),
                   "comments": len(review.get("comments", [])),
                   "blocking_comments": sum(c["severity"] in {"blocker", "major"} for c in review.get("comments", []))},
        "deployment": deploy.get("review", {}),
        "iterations": {"developer": state.get("dev_iterations", 0), "architecture_revisions": state.get("arch_revisions", 0)},
        "route_log": state.get("route_log", []),
        "limitations": limitations,
        "branch": ctx.branch_name,
        "base_commit": ctx.base_commit,
        "commit": commit,
        "sandbox_backend": sandbox,
    }


def pr_body(report: dict[str, Any]) -> str:
    tests = report.get("tests", {})
    lines = [
        "> Prepared by **AgentForge**. Opened as a draft: a human must review and merge.", "",
        f"## Summary\n{report.get('summary') or ''}", "",
        "## Acceptance criteria",
        *[f"- [{'x' if a['status'] == 'verified' else ' '}] **{a['id']}** {a['description']} — _{a['status']}_"
          for a in report["requirements"]["acceptance"]], "",
        "## Tests (executed in the isolated sandbox)",
        (f"{tests.get('passed', 0)} passed · {tests.get('failed', 0)} failed · {tests.get('errors', 0)} errors · "
         f"{tests.get('skipped', 0)} skipped — `{' '.join(tests.get('command', []))}`" if tests.get("executed")
         else f"Not executed: {tests.get('reason')}"), "",
        "## Security",
        f"Scanners: {', '.join(report['security']['tools_run']) or 'none'}. "
        f"Blocking issues open: {len(report['security']['blocking'])}.", "",
        f"## Code review\nVerdict: **{report['review']['verdict']}** — {report['review']['summary'] or ''}", "",
        "## Files changed",
        *[f"- `{f['path']}` (+{f['additions']}/-{f['deletions']})" for f in report["files_modified"]], "",
    ]
    if report["limitations"]:
        lines += ["## Known limitations", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)


def _added_lines(diff: str) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    file, line = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else None
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) - 1 if m else 0
            continue
        if file is None or raw.startswith("-"):
            continue
        line += 1
        if raw.startswith("+"):
            added.setdefault(file, set()).add(line)
    return added


def review_comments(review: dict[str, Any], diff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map reviewer comments onto lines GitHub accepts (lines present in the diff); return the rest separately."""
    added = _added_lines(diff)
    inline, leftover = [], []
    for c in review.get("comments", []):
        body = f"**[{c['severity']}] {c['category']}** — {c['body']}\n\n_AgentForge code reviewer_"
        if c.get("line") in added.get(c.get("file", ""), set()):
            inline.append({"path": c["file"], "line": c["line"], "side": "RIGHT", "body": body})
        else:
            leftover.append(c)
    return inline, leftover


def review_body(review: dict[str, Any], leftover: list[dict[str, Any]]) -> str:
    text = f"### AgentForge code review: {review.get('verdict')}\n\n{review.get('summary', '')}"
    if leftover:
        text += "\n\n**Comments outside the diff:**\n" + "\n".join(
            f"- `{c['file']}:{c['line']}` [{c['severity']}] {c['body']}" for c in leftover)
    return text

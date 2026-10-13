"""Deployment Engineer: deterministic configuration checks + LLM-written deployment plan.

This agent has read-only tools. It cannot deploy, push, or touch infrastructure by construction.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.base import as_json, clip_diff, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import DeploymentReview
from app.agents.tools import read_tools

NAME = "deployment"

_ENV_PATTERNS = [
    re.compile(r"""os\.(?:environ\.get|getenv)\(\s*['"]([A-Z][A-Z0-9_]+)['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]+)['"]\s*\]"""),
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]+)"""),
]
_SETTINGS_FIELD = re.compile(r"^\+\s+([a-z][a-z0-9_]+)\s*:\s*[\w\[\]| .]+=", re.M)


def static_checks(rt: AgentRuntime, diff: str, infra: dict[str, Any]) -> dict[str, Any]:
    ws = rt.workspace
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+"))
    env_vars = sorted({m.group(1) for p in _ENV_PATTERNS for m in p.finditer(added)})
    # pydantic-settings fields added in config modules map to upper-case env vars
    if re.search(r"^\+\+\+ b/.*(settings|config)\.py$", diff, re.M):
        env_vars = sorted(set(env_vars) | {m.group(1).upper() for m in _SETTINGS_FIELD.finditer(diff)})
    documented: set[str] = set()
    for example in infra.get("env_examples", []):
        try:
            text = ws.read_file(example, max_chars=200_000)
        except Exception:  # noqa: S112 - optional file
            continue
        documented |= set(re.findall(r"^\s*\d+\|\s*([A-Z][A-Z0-9_]+)\s*=", text, re.M))
    docker: list[str] = []
    for dockerfile in infra.get("dockerfiles", []):
        try:
            content = (ws.repo / dockerfile).read_text()
        except OSError:
            continue
        froms = re.findall(r"^FROM\s+(\S+)", content, re.M | re.I)
        if any(":" not in f or f.endswith(":latest") for f in froms if f.lower() != "scratch" and not f.startswith("$")):
            docker.append(f"{dockerfile}: base image not pinned to a version tag")
        if not re.search(r"^USER\s+(?!root\b)\S+", content, re.M | re.I):
            docker.append(f"{dockerfile}: container runs as root (no non-root USER)")
        if re.search(r"^(ENV|ARG)\s+\S*(SECRET|PASSWORD|TOKEN|KEY)\S*\s*=?\s*\S+", content, re.M | re.I):
            docker.append(f"{dockerfile}: secret-like value baked into the image via ENV/ARG")
        if not re.search(r"^HEALTHCHECK", content, re.M | re.I):
            docker.append(f"{dockerfile}: no HEALTHCHECK instruction")
    return {
        "env_vars_referenced": env_vars,
        "env_vars_undocumented": [v for v in env_vars if v not in documented] if infra.get("env_examples") else env_vars,
        "env_example_files": infra.get("env_examples", []),
        "docker_findings": docker,
        "dockerfiles": infra.get("dockerfiles", []),
        "compose_files": infra.get("compose", []),
        "ci_files": infra.get("ci", []),
        "migrations_dirs": infra.get("migrations", []),
    }


async def run(rt: AgentRuntime, *, requirements: dict[str, Any], design: dict[str, Any], diff: str,
              checks: dict[str, Any]) -> DeploymentReview:
    prompt = (
        "Prepare the deployment plan for this change. You may read files; you cannot deploy anything. "
        "Call submit when done."
        + section("Deterministic configuration checks", as_json(checks))
        + section("Design (infrastructure-relevant parts)", as_json({
            k: design.get(k) for k in ("dependencies_to_add", "data_model_changes", "api_changes", "risks")}, 8000))
        + section("Requirements", as_json(requirements.get("non_functional_requirements", []), 3000))
        + section("Diff", clip_diff(diff, 30_000))
    )
    return await run_tool_loop(rt, agent=NAME, system=system_prompt(NAME), prompt=prompt, tools=read_tools(rt),
                               output=DeploymentReview, max_tool_calls=min(rt.limits.max_tool_calls, 10),
                               submit_description="Submit the deployment review.")

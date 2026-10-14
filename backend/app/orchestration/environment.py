"""Sandbox dependency environment: per-task virtualenv / node_modules, rebuilt when manifests change."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.agents.runtime import AgentRuntime
from app.intelligence.dependencies import extract_dependencies
from app.sandbox import PolicyViolation, SandboxUnavailable

_MANIFEST_RE = re.compile(r"^(pyproject\.toml|setup\.cfg|setup\.py|requirements[-_.\w]*\.txt|package\.json|"
                          r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml)$")


def manifest_files(rt: AgentRuntime) -> list[str]:
    return sorted(p.name for p in rt.workspace.repo.iterdir() if p.is_file() and _MANIFEST_RE.match(p.name))


def manifest_hash(rt: AgentRuntime) -> str:
    h = hashlib.sha256()
    for name in manifest_files(rt):
        h.update(name.encode())
        h.update((rt.workspace.repo / name).read_bytes())
    return h.hexdigest()[:16]


async def ensure_environment(rt: AgentRuntime, state: dict[str, Any]) -> dict[str, Any]:
    current = state.get("environment") or {}
    digest = manifest_hash(rt)
    if current.get("manifest_hash") == digest and current.get("ok") is not None:
        return current

    testing = state.get("testing", {})
    manifests = manifest_files(rt)
    paths = rt.workspace.sandbox_paths
    commands: list[dict[str, Any]] = []
    error: str | None = None

    async def step(argv: list[str], *, network: bool) -> bool:
        nonlocal error
        try:
            res = await rt.execute(argv, agent=None, purpose="install", allow_network=network, timeout_s=900)
        except (PolicyViolation, SandboxUnavailable) as exc:
            error = str(exc)
            commands.append({"argv": argv, "ok": False, "error": error})
            return False
        commands.append({"argv": argv, "ok": res.ok, "exit_code": res.exit_code, "duration_ms": res.duration_ms,
                         "network": res.network, "stderr_tail": res.stderr[-600:]})
        if not res.ok:
            error = f"`{' '.join(argv)}` failed (exit {res.exit_code})"
        return res.ok

    is_python = "pytest" in testing.get("frameworks", []) or any(
        m in manifests for m in ("pyproject.toml", "setup.py")) or any(m.startswith("requirements") for m in manifests)
    is_node = "package.json" in manifests and not is_python
    if is_python:
        venv = f"{rt.sandbox.deps_dir(paths)}/venv"
        if not (paths.deps / "venv" / "pyvenv.cfg").exists():
            await step(["python3", "-m", "venv", venv], network=False)
        if error is None:
            await step(["python", "-m", "pip", "install", "--quiet", "--upgrade", "pip"], network=True)
            for req in [m for m in manifests if m.startswith("requirements")]:
                await step(["python", "-m", "pip", "install", "--quiet", "-r", req], network=True)
            if "pyproject.toml" in manifests:
                deps = [d for d in extract_dependencies(rt.workspace.repo, ["pyproject.toml"]) if d.ecosystem == "pypi"]
                if deps:
                    req_file = rt.workspace.scratch / "requirements-pyproject.txt"
                    req_file.write_text("\n".join(f"{d.name}{d.spec}" for d in deps) + "\n")
                    await step(["python", "-m", "pip", "install", "--quiet", "-r",
                                ".agentforge/requirements-pyproject.txt"], network=True)
            await step(["python", "-m", "pip", "install", "--quiet", "pytest"], network=True)
    elif is_node:
        lock = "package-lock.json" in manifests
        await step(["npm", "ci" if lock else "install", "--no-audit", "--no-fund"], network=True)

    env = {
        "ok": error is None and (is_python or is_node),
        "kind": "python" if is_python else "node" if is_node else "unknown",
        "manifest_hash": digest,
        "commands": commands,
        "error": error if (is_python or is_node) else "No supported dependency manifest found",
        "test_command": testing.get("test_command"),
        "junit": bool(testing.get("junit_supported")),
        "sandbox": rt.sandbox.name,
    }
    await rt.emit("environment", ("Sandbox environment ready" if env["ok"] else f"Environment setup problem: {env['error']}"),
                  payload=env)
    return env

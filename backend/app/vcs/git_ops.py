"""Git operations via the git CLI, hardened for untrusted repositories.

* The user's global/system git config is ignored (no credential helpers, no aliases).
* Hooks are disabled for every command; submodules are never fetched.
* Tokens are passed through GIT_CONFIG_* environment variables, never argv or .git/config,
  and are redacted from any error output.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.security import redact


class GitError(Exception):
    pass


@dataclass
class ChangedFile:
    path: str
    status: str  # A, M, D, R
    additions: int
    deletions: int


def _env(extra_config: dict[str, str], token: str | None) -> dict[str, str]:
    config = {
        "core.hooksPath": "/dev/null",
        "protocol.file.allow": "never",
        "submodule.recurse": "false",
        "commit.gpgsign": "false",
        "tag.gpgsign": "false",
        "init.defaultBranch": "main",
        "advice.detachedHead": "false",
        **extra_config,
    }
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        config["http.https://github.com/.extraheader"] = f"AUTHORIZATION: basic {basic}"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108  # nosec B108 - only a fallback
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": str(len(config)),
        "LC_ALL": "C",
    }
    for i, (k, v) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


async def git(
    *args: str,
    cwd: Path | None = None,
    token: str | None = None,
    config: dict[str, str] | None = None,
    timeout: float = 300,
    check: bool = True,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, env=_env(config or {}, token),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        message = redact(err.decode(errors="replace").strip() or out.decode(errors="replace"), [token or ""])
        raise GitError(f"git {args[0]} failed: {message[-2000:]}")
    return out.decode(errors="replace")


async def clone(source: str, dest: Path, *, branch: str | None = None, token: str | None = None,
                local: bool = False, depth: int | None = 50) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--no-recurse-submodules"]
    if depth and not local:
        args += ["--depth", str(depth)]
    if branch:
        args += ["--branch", branch]
    args += ["--", source, str(dest)]
    config = {"protocol.file.allow": "always"} if local else {}
    await git(*args, token=token, config=config, timeout=600)
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as fh:
        fh.write("\n# AgentForge working files\n.agentforge/\n")


async def init_from_directory(source: Path, dest: Path, author: tuple[str, str]) -> None:
    """Snapshot a plain (non-git) directory into a fresh repository."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, symlinks=False, ignore=shutil.ignore_patterns(
        ".git", "node_modules", ".venv", "venv", "__pycache__", ".agentforge"))
    await git("init", "-q", cwd=dest)
    await git("add", "-A", cwd=dest)
    await commit(dest, "Initial snapshot", author)
    (dest / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (dest / ".git" / "info" / "exclude").write_text(".agentforge/\n")


async def head_commit(repo: Path) -> str:
    return (await git("rev-parse", "HEAD", cwd=repo)).strip()


async def current_branch(repo: Path) -> str:
    return (await git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)).strip()


async def create_branch(repo: Path, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,200}", name) or ".." in name:
        raise GitError(f"invalid branch name {name!r}")
    await git("checkout", "-q", "-B", name, cwd=repo)


async def stage_all(repo: Path) -> None:
    # .agentforge/ (platform scratch files) is excluded through .git/info/exclude.
    await git("add", "-A", cwd=repo)


async def diff(repo: Path, base: str, *, paths: list[str] | None = None, context: int = 3) -> str:
    await stage_all(repo)
    return await git("diff", "--cached", f"-U{context}", "--no-color", base, "--", *(paths or []), cwd=repo)


async def changed_files(repo: Path, base: str) -> list[ChangedFile]:
    await stage_all(repo)
    numstat = await git("diff", "--cached", "--numstat", "-M", base, cwd=repo)
    status = await git("diff", "--cached", "--name-status", "-M", base, cwd=repo)
    statuses: dict[str, str] = {}
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            statuses[parts[-1]] = parts[0][0]
    out = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds, dels, path = parts[0], parts[1], parts[-1]
        if "=>" in path:  # rename notation "a => b" or "dir/{a => b}"
            path = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", path)
            path = path.split(" => ")[-1]
        out.append(ChangedFile(path, statuses.get(path, "M"),
                               int(adds) if adds.isdigit() else 0, int(dels) if dels.isdigit() else 0))
    return out


async def commit(repo: Path, message: str, author: tuple[str, str]) -> str:
    name, email = author
    await stage_all(repo)
    await git("commit", "-q", "--no-verify", "--allow-empty-message", "-m", message, cwd=repo,
              config={"user.name": name, "user.email": email})
    return await head_commit(repo)


async def has_changes(repo: Path, base: str) -> bool:
    await stage_all(repo)
    return bool((await git("diff", "--cached", "--name-only", base, cwd=repo)).strip())


async def push(repo: Path, branch: str, *, token: str | None, protected: set[str]) -> None:
    if branch in protected or not branch.startswith("agentforge/"):
        raise GitError(f"refusing to push to protected or non-AgentForge branch {branch!r}")
    await git("push", "--no-verify", "origin", f"HEAD:refs/heads/{branch}", cwd=repo, token=token, timeout=300)

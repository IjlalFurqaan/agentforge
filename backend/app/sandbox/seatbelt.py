"""macOS Seatbelt (sandbox-exec) backend — DEVELOPMENT ONLY.

Kernel-enforced restrictions per command:
  * no network (except for explicitly approved install/audit steps),
  * writes only inside the task's repo / deps / tmp directories,
  * reads of the user's home directory denied, except the task directories and the interpreter,
  * scrubbed environment, CPU-time and file-size rlimits, wall-clock timeout with process-group kill.

It shares the host kernel and filesystem view, so it is weaker than a container and refuses to run
when ENVIRONMENT=production (see Settings.validate_for_runtime).
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import signal
import sys
import time
from pathlib import Path

from app.core.config import Settings
from app.sandbox.base import ExecResult, OutputBuffer, SandboxPaths, SandboxRunner, SandboxUnavailable, pump
from app.sandbox.policy import ValidatedCommand

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _sb_quote(path: Path | str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_profile(paths: SandboxPaths, *, network: bool, extra_read: list[Path]) -> str:
    home = Path.home().resolve()
    writable = [paths.repo, paths.deps, paths.tmp]
    readable = [*writable, *extra_read]
    rules = [
        "(version 1)",
        "(allow default)",
        # Filesystem: deny all writes, then allow the task directories and terminal devices.
        "(deny file-write*)",
        "(allow file-write* " + " ".join(f"(subpath {_sb_quote(p)})" for p in writable)
        + ' (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (regex #"^/dev/fd/"))',
        # Secrets live in $HOME (ssh keys, cloud creds, browser profiles): deny, then re-allow task dirs.
        f"(deny file-read* (subpath {_sb_quote(home)}))",
        "(allow file-read* " + " ".join(f"(subpath {_sb_quote(p)})" for p in readable) + ")",
        # The home directory node itself must be stat-able for path resolution.
        f"(allow file-read-metadata (literal {_sb_quote(home)}))",
        "(deny process-exec (literal \"/usr/bin/sudo\") (literal \"/usr/bin/osascript\") (literal \"/usr/bin/ssh\"))",
    ]
    if not network:
        rules.append("(deny network*)")
    return "\n".join(rules)


def _limits(cpu_seconds: int, fsize_bytes: int) -> None:  # runs in the child before exec
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class SeatbeltSandbox(SandboxRunner):
    name = "seatbelt"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def healthcheck(self) -> None:
        if sys.platform != "darwin" or not os.path.exists(SANDBOX_EXEC):
            raise SandboxUnavailable("sandbox-exec is only available on macOS")

    def _interpreter_paths(self) -> list[Path]:
        """Directories the sandboxed process must read to run the configured Python."""
        out: set[Path] = set()
        py = shutil.which(self.settings.sandbox_python)
        if py:
            real = Path(py).resolve()
            out.add(real.parents[1])  # e.g. /opt/homebrew/Cellar/python@3.x/<ver>/Frameworks/...
        out.add(Path(sys.prefix).resolve())        # backend venv (bandit, etc.) — contains no secrets
        out.add(Path(sys.base_prefix).resolve())
        return sorted(out)

    async def run(self, cmd: ValidatedCommand, paths: SandboxPaths, *, timeout_s: int,
                  env: dict[str, str] | None = None) -> ExecResult:
        await self.healthcheck()
        for p in (paths.repo, paths.deps, paths.tmp):
            p.mkdir(parents=True, exist_ok=True)
        cwd = (paths.repo / cmd.workdir).resolve()
        if not cwd.is_relative_to(paths.repo.resolve()) or not cwd.is_dir():
            raise SandboxUnavailable(f"invalid working directory {cmd.workdir!r}")

        venv_bin = paths.deps / "venv" / "bin"
        node_bin = paths.repo / "node_modules" / ".bin"
        clean_env = {
            # Platform tools (bandit) come from the backend's own virtualenv, after the task's.
            "PATH": os.pathsep.join([str(venv_bin), str(node_bin), "/opt/homebrew/bin", "/usr/local/bin",
                                     "/usr/bin", "/bin", str(Path(sys.prefix) / "bin")]),
            "HOME": str(paths.tmp),
            "TMPDIR": str(paths.tmp),
            "LANG": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_CACHE_DIR": str(paths.deps / "pip-cache"),
            "npm_config_cache": str(paths.deps / "npm-cache"),
            "CI": "1",
            **(env or {}),
        }
        if venv_bin.exists():
            clean_env["VIRTUAL_ENV"] = str(paths.deps / "venv")
        profile = build_profile(paths.__class__(paths.repo.resolve(), paths.deps.resolve(), paths.tmp.resolve()),
                                network=cmd.network, extra_read=self._interpreter_paths())
        argv = [SANDBOX_EXEC, "-p", profile, *cmd.argv]
        if cmd.argv[0] in {"python", "python3"} and not venv_bin.exists():
            argv[3] = shutil.which(self.settings.sandbox_python) or cmd.argv[0]

        started = time.monotonic()
        out, err = OutputBuffer(self.settings.sandbox_output_limit_bytes), OutputBuffer(self.settings.sandbox_output_limit_bytes)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=clean_env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
            preexec_fn=lambda: _limits(timeout_s * 2, 1 << 30),
        )
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.gather(pump(proc.stdout, out), pump(proc.stderr, err), proc.wait()),
                                   timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        return ExecResult(
            exit_code=None if timed_out else proc.returncode,
            stdout=out.text(), stderr=err.text(),
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out, backend=self.name, network=cmd.network,
            truncated=bool(out.dropped or err.dropped),
        )

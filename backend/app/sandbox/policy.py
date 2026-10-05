"""Command allowlist enforced before anything reaches a sandbox backend.

The sandbox is the security boundary; this policy is defence in depth that keeps agents to the
small set of tools a build/test loop needs, and makes intent auditable.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

ALLOWED_EXECUTABLES = frozenset({
    "python", "python3", "pip", "pip3", "pytest", "ruff", "mypy", "bandit", "semgrep", "pip-audit",
    "node", "npm", "npx", "yarn", "pnpm", "tsc", "jest", "vitest", "eslint",
    "go", "cargo", "make", "ls", "cat", "head", "tail", "wc", "grep", "find", "diff", "git",
})
DENIED_EXECUTABLES = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "curl", "wget", "nc", "ncat", "netcat", "ssh",
    "scp", "sftp", "telnet", "sudo", "su", "doas", "docker", "podman", "kubectl", "chmod", "chown",
    "mount", "osascript", "open", "launchctl", "crontab", "rm",
})
READONLY_GIT_SUBCOMMANDS = frozenset({"status", "diff", "log", "show", "ls-files", "rev-parse", "blame"})
# `python -m X` is only allowed for these modules.
ALLOWED_PYTHON_MODULES = frozenset({
    "pytest", "pip", "unittest", "venv", "compileall", "bandit", "mypy", "ruff", "pip_audit", "alembic",
    "coverage", "doctest", "py_compile", "black", "isort",
})
NETWORK_PURPOSES = frozenset({"install", "audit"})
MAX_ARGS = 64
MAX_ARG_LEN = 4096


class PolicyViolation(Exception):
    pass


@dataclass(frozen=True)
class ValidatedCommand:
    argv: tuple[str, ...]
    workdir: str
    network: bool


def normalise_workdir(workdir: str) -> str:
    wd = posixpath.normpath(workdir or ".")
    if wd.startswith("/") or wd == ".." or wd.startswith("../"):
        raise PolicyViolation(f"workdir must be relative to the repository root: {workdir!r}")
    return wd


def validate_command(argv: list[str], *, workdir: str = ".", purpose: str = "run",
                     allow_network: bool = False) -> ValidatedCommand:
    if not argv:
        raise PolicyViolation("empty command")
    if len(argv) > MAX_ARGS or any(len(a) > MAX_ARG_LEN for a in argv):
        raise PolicyViolation("command too long")
    if any("\x00" in a for a in argv):
        raise PolicyViolation("NUL byte in arguments")
    exe = argv[0]
    if "/" in exe:
        # Allow only venv-local interpreters such as .venv/bin/python, never absolute host paths.
        base = posixpath.basename(exe)
        if exe.startswith("/") or ".." in exe.split("/"):
            raise PolicyViolation(f"executable paths must be bare names: {exe!r}")
    else:
        base = exe
    if base in DENIED_EXECUTABLES or base not in ALLOWED_EXECUTABLES:
        raise PolicyViolation(f"executable {base!r} is not allowed in the sandbox")

    if base in {"python", "python3"}:
        if len(argv) >= 2 and argv[1] == "-c":
            raise PolicyViolation("python -c is not allowed; write a script or test file instead")
        if len(argv) >= 3 and argv[1] == "-m":
            module = argv[2].split(".", 1)[0]
            if module not in ALLOWED_PYTHON_MODULES:
                raise PolicyViolation(f"python -m {argv[2]} is not allowed")
    if base == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), None)
        if sub not in READONLY_GIT_SUBCOMMANDS:
            raise PolicyViolation("only read-only git subcommands are allowed in the sandbox")
    if base in {"find"} and any(a in {"-exec", "-execdir", "-delete", "-ok"} for a in argv):
        raise PolicyViolation("find with -exec/-delete is not allowed")
    if base in {"npx"} and not allow_network and len(argv) > 1 and argv[1] in {"-y", "--yes"}:
        raise PolicyViolation("npx package downloads require an install step")

    network = allow_network and purpose in NETWORK_PURPOSES
    return ValidatedCommand(argv=tuple(argv), workdir=normalise_workdir(workdir), network=network)

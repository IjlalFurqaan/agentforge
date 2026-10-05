"""Sandbox abstraction shared by all execution backends."""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.sandbox.policy import ValidatedCommand


class SandboxUnavailable(Exception):
    """The configured backend cannot run commands (e.g. Docker daemon not reachable)."""


@dataclass(frozen=True)
class SandboxPaths:
    repo: Path  # mounted read-write at the sandbox's working directory
    deps: Path  # per-task dependency cache (virtualenv, npm cache)
    tmp: Path   # per-task scratch


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    backend: str
    network: bool
    truncated: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxRunner(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    async def run(self, cmd: ValidatedCommand, paths: SandboxPaths, *, timeout_s: int,
                  env: dict[str, str] | None = None) -> ExecResult: ...

    async def healthcheck(self) -> None:  # noqa: B027 - optional override
        """Raise SandboxUnavailable when the backend cannot run commands."""

    def deps_dir(self, paths: SandboxPaths) -> str:
        """Path of the per-task dependency directory as seen from inside the sandbox."""
        return str(paths.deps.resolve())


class OutputBuffer:
    """Keeps the head and tail of a stream so huge outputs cannot exhaust memory."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = 0

    def feed(self, chunk: bytes) -> None:
        half = self.limit // 2
        if len(self.head) < half:
            take = half - len(self.head)
            self.head += chunk[:take]
            chunk = chunk[take:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > half:
                self.dropped += len(self.tail) - half
                del self.tail[: len(self.tail) - half]

    def text(self) -> str:
        middle = f"\n... [{self.dropped} bytes truncated] ...\n".encode() if self.dropped else b""
        return bytes(self.head + middle + self.tail).decode("utf-8", errors="replace")


async def pump(stream: asyncio.StreamReader | None, buf: OutputBuffer) -> None:
    if stream is None:
        return
    while chunk := await stream.read(65536):
        buf.feed(chunk)

from __future__ import annotations

from app.core.config import Settings
from app.sandbox.base import ExecResult, SandboxPaths, SandboxRunner, SandboxUnavailable
from app.sandbox.policy import PolicyViolation, ValidatedCommand, validate_command


class DisabledSandbox(SandboxRunner):
    name = "disabled"

    async def healthcheck(self) -> None:
        raise SandboxUnavailable("Sandbox execution is disabled (SANDBOX_BACKEND=disabled)")

    async def run(self, cmd, paths, *, timeout_s, env=None):  # type: ignore[no-untyped-def]
        raise SandboxUnavailable("Sandbox execution is disabled (SANDBOX_BACKEND=disabled)")


def build_sandbox(settings: Settings) -> SandboxRunner:
    if settings.sandbox_backend == "docker":
        from app.sandbox.docker_backend import DockerSandbox

        return DockerSandbox(settings)
    if settings.sandbox_backend == "seatbelt":
        from app.sandbox.seatbelt import SeatbeltSandbox

        return SeatbeltSandbox(settings)
    return DisabledSandbox()


__all__ = [
    "ExecResult", "PolicyViolation", "SandboxPaths", "SandboxRunner", "SandboxUnavailable",
    "ValidatedCommand", "build_sandbox", "validate_command",
]

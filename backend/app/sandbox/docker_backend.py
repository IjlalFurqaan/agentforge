"""Docker backend: one short-lived, locked-down container per command."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.sandbox.base import ExecResult, SandboxPaths, SandboxRunner, SandboxUnavailable
from app.sandbox.policy import ValidatedCommand

CONTAINER_REPO = "/workspace"
CONTAINER_DEPS = "/deps"
SANDBOX_UID = 10001


class DockerSandbox(SandboxRunner):
    name = "docker"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None

    def _docker(self) -> Any:
        if self._client is None:
            try:
                import docker  # optional dependency: pip install agentforge[docker]
            except ImportError as exc:
                raise SandboxUnavailable("docker SDK not installed (pip install 'agentforge[docker]')") from exc
            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:
                self._client = None
                raise SandboxUnavailable(f"Docker daemon not reachable: {exc}") from exc
        return self._client

    async def healthcheck(self) -> None:
        await asyncio.to_thread(self._docker)

    def deps_dir(self, paths: SandboxPaths) -> str:
        return CONTAINER_DEPS

    def _host_path(self, path: Path) -> str:
        """Translate a worker-side path to the Docker host path (worker may itself run in a container)."""
        if self.settings.sandbox_host_workspaces_root is None:
            return str(path.resolve())
        rel = path.resolve().relative_to(self.settings.workspaces_root.resolve())
        return str(self.settings.sandbox_host_workspaces_root / rel)

    def _run_sync(self, cmd: ValidatedCommand, paths: SandboxPaths, timeout_s: int,
                  env: dict[str, str]) -> ExecResult:
        from docker.types import Mount

        client = self._docker()
        user = f"{os.getuid()}:{os.getgid()}" if os.getuid() != 0 else f"{SANDBOX_UID}:{SANDBOX_UID}"
        container = client.containers.create(
            image=self.settings.sandbox_image,
            command=list(cmd.argv),
            working_dir=f"{CONTAINER_REPO}/{cmd.workdir}".rstrip("/."),
            user=user,
            environment={
                "HOME": "/tmp",  # noqa: S108  # nosec B108 - container-private tmpfs
                "PATH": f"{CONTAINER_DEPS}/venv/bin:{CONTAINER_REPO}/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                "PIP_CACHE_DIR": f"{CONTAINER_DEPS}/pip-cache", "npm_config_cache": f"{CONTAINER_DEPS}/npm-cache",
                "CI": "1", **env,
            },
            network_mode="bridge" if cmd.network else "none",
            mounts=[
                Mount(CONTAINER_REPO, self._host_path(paths.repo), type="bind", read_only=False),
                Mount(CONTAINER_DEPS, self._host_path(paths.deps), type="bind", read_only=False),
            ],
            read_only=True,
            tmpfs={"/tmp": "rw,nosuid,nodev,size=512m"},  # noqa: S108  # nosec B108 - container-private tmpfs
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=f"{self.settings.sandbox_memory_mb}m",
            memswap_limit=f"{self.settings.sandbox_memory_mb}m",
            nano_cpus=int(self.settings.sandbox_cpus * 1e9),
            pids_limit=self.settings.sandbox_pids_limit,
            ulimits=[],
            runtime=self.settings.sandbox_docker_runtime,
            labels={"agentforge.sandbox": "1"},
            detach=True,
        )
        started = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        try:
            container.start()
            try:
                exit_code = int(container.wait(timeout=timeout_s)["StatusCode"])
            except Exception:  # requests ReadTimeout / ConnectionError on timeout
                timed_out = True
                container.kill()
            limit = self.settings.sandbox_output_limit_bytes
            stdout = container.logs(stdout=True, stderr=False)[-limit:].decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True)[-limit:].decode("utf-8", errors="replace")
        finally:
            container.remove(force=True)
        return ExecResult(exit_code=None if timed_out else exit_code, stdout=stdout, stderr=stderr,
                          duration_ms=int((time.monotonic() - started) * 1000), timed_out=timed_out,
                          backend=self.name, network=cmd.network)

    async def run(self, cmd: ValidatedCommand, paths: SandboxPaths, *, timeout_s: int,
                  env: dict[str, str] | None = None) -> ExecResult:
        for p in (paths.repo, paths.deps, paths.tmp):
            p.mkdir(parents=True, exist_ok=True)
        if os.getuid() == 0:
            # Sandboxed code never runs as root: hand the task directories to an unprivileged uid.
            await asyncio.to_thread(_chown_tree, [paths.repo, paths.deps, paths.tmp], SANDBOX_UID)
        return await asyncio.to_thread(self._run_sync, cmd, paths, timeout_s, env or {})


def _chown_tree(roots: list[Path], uid: int) -> None:
    for root in roots:
        os.chown(root, uid, uid)
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                os.chown(os.path.join(dirpath, name), uid, uid, follow_symlinks=False)

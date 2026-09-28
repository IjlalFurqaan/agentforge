"""Per-task isolated workspace with traversal-safe file operations."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.intelligence.scanner import IGNORED_DIRS, scan_repository
from app.intelligence.search import CodeIndex
from app.sandbox.base import SandboxPaths


class WorkspaceError(Exception):
    pass


TEST_PATH_PATTERNS = (
    "tests/*", "test/*", "*/tests/*", "*/test/*", "test_*.py", "*/test_*.py", "*_test.py", "*/__tests__/*",
    "*.test.ts", "*.test.tsx", "*.test.js", "*.spec.ts", "*.spec.js", "conftest.py", "*/conftest.py",
    "pytest.ini", "jest.config.*", "vitest.config.*",
)


@dataclass
class Workspace:
    root: Path  # <workspaces_root>/<task_id>
    _index: CodeIndex | None = field(default=None, repr=False)

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def sandbox_paths(self) -> SandboxPaths:
        return SandboxPaths(repo=self.repo, deps=self.root / "deps", tmp=self.root / "tmp")

    @property
    def scratch(self) -> Path:
        path = self.repo / ".agentforge"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, rel: str, *, for_write: bool = False) -> Path:
        if not rel or "\x00" in rel:
            raise WorkspaceError("empty or invalid path")
        candidate = Path(rel)
        if candidate.is_absolute():
            raise WorkspaceError(f"absolute paths are not allowed: {rel}")
        repo = self.repo.resolve()
        target = (repo / candidate).resolve()  # resolves symlinks, so links out of the repo are caught
        if target != repo and not target.is_relative_to(repo):
            raise WorkspaceError(f"path escapes the repository: {rel}")
        parts = target.relative_to(repo).parts
        if parts and parts[0] == ".git":
            raise WorkspaceError("the .git directory is off-limits")
        if for_write and parts and parts[0] == ".agentforge":
            raise WorkspaceError(".agentforge is reserved for platform files")
        return target

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo.resolve()).as_posix()

    # --- read -------------------------------------------------------------------------------------

    def list_dir(self, rel: str = ".", max_entries: int = 300) -> list[str]:
        target = self.resolve(rel)
        if not target.is_dir():
            raise WorkspaceError(f"not a directory: {rel}")
        out = []
        for entry in sorted(target.iterdir()):
            if entry.name in IGNORED_DIRS or entry.name == ".git":
                continue
            out.append(entry.name + ("/" if entry.is_dir() else ""))
            if len(out) >= max_entries:
                out.append("... (truncated)")
                break
        return out

    def read_file(self, rel: str, start: int | None = None, end: int | None = None, max_chars: int = 40_000) -> str:
        target = self.resolve(rel)
        if not target.is_file():
            raise WorkspaceError(f"file not found: {rel}")
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        lo = max((start or 1) - 1, 0)
        hi = min(end or len(lines), len(lines))
        numbered = "\n".join(f"{i + 1:>5}| {line}" for i, line in enumerate(lines[lo:hi], start=lo))
        if len(numbered) > max_chars:
            numbered = numbered[:max_chars] + f"\n... [truncated; file has {len(lines)} lines, use start/end]"
        return numbered or "(empty file)"

    def grep(self, pattern: str, glob: str | None = None, max_hits: int = 60) -> list[str]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"invalid regex: {exc}") from exc
        hits: list[str] = []
        for entry in scan_repository(self.repo, max_files=10_000).files:
            if entry.is_binary or (glob and not fnmatch.fnmatch(entry.path, glob)):
                continue
            try:
                lines = (self.repo / entry.path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                if rx.search(line):
                    hits.append(f"{entry.path}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def index(self) -> CodeIndex:
        if self._index is None:
            self._index = CodeIndex.build(self.repo, scan_repository(self.repo))
        return self._index

    def invalidate_index(self) -> None:
        self._index = None

    # --- write ------------------------------------------------------------------------------------

    def write_file(self, rel: str, content: str, *, test_only: bool = False) -> str:
        target = self.resolve(rel, for_write=True)
        rel_posix = self.rel(target) if target.exists() else Path(rel).as_posix()
        if test_only and not is_test_path(rel_posix):
            raise WorkspaceError(f"this agent may only write test files; {rel_posix} is not a test path")
        if len(content) > 400_000:
            raise WorkspaceError("file too large")
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        self.invalidate_index()
        return "updated" if existed else "created"

    def edit_file(self, rel: str, old: str, new: str, *, test_only: bool = False) -> int:
        target = self.resolve(rel, for_write=True)
        if test_only and not is_test_path(self.rel(target)):
            raise WorkspaceError(f"this agent may only edit test files; {rel} is not a test path")
        if not target.is_file():
            raise WorkspaceError(f"file not found: {rel}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise WorkspaceError("old_text not found; read the file again and copy the text exactly")
        if count > 1:
            raise WorkspaceError(f"old_text matches {count} places; include more surrounding context")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        self.invalidate_index()
        return 1

    def delete_file(self, rel: str) -> None:
        target = self.resolve(rel, for_write=True)
        if not target.is_file():
            raise WorkspaceError(f"file not found: {rel}")
        target.unlink()
        self.invalidate_index()


def is_test_path(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in TEST_PATH_PATTERNS)


def workspace_for(workspaces_root: Path, task_id: str) -> Workspace:
    root = (workspaces_root / task_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return Workspace(root=root)

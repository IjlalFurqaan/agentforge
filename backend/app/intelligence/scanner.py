"""File-system scan with language detection and binary filtering."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env.d", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "dist", "build", ".next", ".nuxt",
    ".svelte-kit", "target", "vendor", "coverage", "htmlcov", ".idea", ".vscode", ".agentforge",
    "site-packages", ".gradle", ".terraform", ".turbo", ".parcel-cache", ".cache", "bower_components",
})

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyi": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift",
    ".m": "Objective-C", ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".mdx": "Markdown", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON",
    ".toml": "TOML", ".tf": "HCL", ".proto": "Protocol Buffers", ".graphql": "GraphQL",
    ".ini": "INI", ".cfg": "INI", ".xml": "XML", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir",
}
FILENAME_LANGUAGES: dict[str, str] = {
    "Dockerfile": "Dockerfile", "Makefile": "Makefile", "Jenkinsfile": "Groovy", "Procfile": "Procfile",
}
# Languages that count as "source code" for the language breakdown.
PROGRAMMING_LANGUAGES = frozenset({
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin", "Scala", "Ruby", "PHP", "C#",
    "C", "C++", "Swift", "Objective-C", "SQL", "Shell", "Vue", "Svelte", "Dart", "Elixir", "HCL",
})


@dataclass
class FileEntry:
    path: str  # POSIX path relative to the repo root
    size: int
    language: str | None
    lines: int = 0
    is_binary: bool = False


@dataclass
class ScanResult:
    root: Path
    files: list[FileEntry] = field(default_factory=list)
    truncated: bool = False

    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}


def detect_language(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if name in FILENAME_LANGUAGES or name.startswith("Dockerfile"):
        return FILENAME_LANGUAGES.get(name, "Dockerfile")
    return EXTENSION_LANGUAGES.get(os.path.splitext(name)[1].lower())


def scan_repository(root: Path, *, max_files: int = 20_000, max_file_bytes: int = 1_000_000) -> ScanResult:
    root = root.resolve()
    result = ScanResult(root=root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            rel = full.relative_to(root).as_posix()
            try:
                size = full.stat().st_size
            except OSError:
                continue
            entry = FileEntry(path=rel, size=size, language=detect_language(rel))
            if size <= max_file_bytes:
                try:
                    with full.open("rb") as fh:
                        data = fh.read()
                    entry.is_binary = b"\0" in data[:8192]
                    if not entry.is_binary:
                        entry.lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
                except OSError:
                    continue
            else:
                entry.is_binary = True  # treat oversized files as opaque
            result.files.append(entry)
            if len(result.files) >= max_files:
                result.truncated = True
                return result
    return result


def read_text(root: Path, rel: str, max_bytes: int = 1_000_000) -> str | None:
    path = root / rel
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

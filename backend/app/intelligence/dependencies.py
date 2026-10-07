"""Dependency manifest parsing (Python, Node, Go, Rust, Ruby)."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[^\]]*\])?\s*(.*?)\s*(?:;.*)?$")


@dataclass(frozen=True)
class Dependency:
    name: str
    spec: str
    ecosystem: str  # pypi | npm | go | cargo | rubygems
    dev: bool
    source: str  # manifest file

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_requirement(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http://", "https://")):
        return None
    m = _REQ_LINE.match(line)
    if not m:
        return None
    return m.group(1).lower().replace("_", "-"), (m.group(3) or "").strip()


def parse_requirements_txt(text: str, source: str) -> list[Dependency]:
    dev = any(k in source.lower() for k in ("dev", "test", "lint"))
    deps = []
    for line in text.splitlines():
        parsed = _parse_requirement(line)
        if parsed:
            deps.append(Dependency(parsed[0], parsed[1], "pypi", dev, source))
    return deps


def parse_pyproject(text: str, source: str) -> list[Dependency]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    deps: list[Dependency] = []
    project = data.get("project", {})
    for req in project.get("dependencies", []) or []:
        if parsed := _parse_requirement(req):
            deps.append(Dependency(parsed[0], parsed[1], "pypi", False, source))
    for group, reqs in (project.get("optional-dependencies", {}) or {}).items():
        for req in reqs:
            if parsed := _parse_requirement(req):
                deps.append(Dependency(parsed[0], parsed[1], "pypi", group in {"dev", "test", "tests", "lint"}, source))
    for reqs in (data.get("dependency-groups", {}) or {}).values():  # PEP 735
        for req in reqs:
            if isinstance(req, str) and (parsed := _parse_requirement(req)):
                deps.append(Dependency(parsed[0], parsed[1], "pypi", True, source))
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() != "python":
            deps.append(Dependency(name.lower(), spec if isinstance(spec, str) else json.dumps(spec), "pypi", False, source))
    for group in (poetry.get("group", {}) or {}).values():
        for name, spec in (group.get("dependencies", {}) or {}).items():
            deps.append(Dependency(name.lower(), spec if isinstance(spec, str) else json.dumps(spec), "pypi", True, source))
    return deps


def parse_package_json(text: str, source: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps = []
    for key, dev in (("dependencies", False), ("devDependencies", True), ("peerDependencies", False)):
        for name, spec in (data.get(key) or {}).items():
            deps.append(Dependency(name, str(spec), "npm", dev, source))
    return deps


def parse_go_mod(text: str, source: str) -> list[Dependency]:
    deps = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require ") and not line.endswith("("):
            line = line[len("require "):]
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            deps.append(Dependency(parts[0], parts[1], "go", "// indirect" in raw, source))
    return deps


def parse_cargo_toml(text: str, source: str) -> list[Dependency]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    deps = []
    for key, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (data.get(key) or {}).items():
            version = spec if isinstance(spec, str) else str(spec.get("version", "")) if isinstance(spec, dict) else ""
            deps.append(Dependency(name, version, "cargo", dev, source))
    return deps


def parse_gemfile(text: str, source: str) -> list[Dependency]:
    deps = []
    for m in re.finditer(r"""^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""", text, re.M):
        deps.append(Dependency(m.group(1), m.group(2) or "", "rubygems", False, source))
    return deps


_PARSERS = {
    "pyproject.toml": parse_pyproject,
    "package.json": parse_package_json,
    "go.mod": parse_go_mod,
    "Cargo.toml": parse_cargo_toml,
    "Gemfile": parse_gemfile,
}


def extract_dependencies(root: Path, paths: list[str]) -> list[Dependency]:
    deps: list[Dependency] = []
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        parser = _PARSERS.get(name)
        if parser is None and re.fullmatch(r"requirements([-_.\w]*)\.txt", name):
            parser = parse_requirements_txt
        if parser is None:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        deps.extend(parser(text, rel))
    # De-duplicate by (ecosystem, name), preferring runtime over dev declarations.
    unique: dict[tuple[str, str], Dependency] = {}
    for d in deps:
        key = (d.ecosystem, d.name)
        if key not in unique or (unique[key].dev and not d.dev):
            unique[key] = d
    return sorted(unique.values(), key=lambda d: (d.ecosystem, d.dev, d.name))

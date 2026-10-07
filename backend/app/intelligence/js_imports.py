"""JavaScript / TypeScript import extraction and resolution.

This is a lexical analyser (regular expressions over comment-stripped source), not a full parser.
It handles ES module imports/exports, `require()`, dynamic `import()` and tsconfig `paths` aliases,
which covers dependency-graph construction for the vast majority of real-world code.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import asdict, dataclass, field
from typing import Any

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:[\w*{}\s,]+?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)""",
)
_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(function\*?|class|const|let|var|interface|type|enum)\s+(\w+)", re.M
)
_EXPRESS_ROUTE_RE = re.compile(r"""\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]""")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRING_RE = re.compile(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`""", re.S)


@dataclass
class JsModule:
    path: str
    imports: list[str] = field(default_factory=list)  # raw specifiers
    exports: list[dict[str, str]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_comments(source: str) -> str:
    """Remove comments while leaving string literals (which may contain `//`) untouched."""
    out: list[str] = []
    pos = 0
    for m in _STRING_RE.finditer(source):
        out.append(_COMMENT_RE.sub(" ", source[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_COMMENT_RE.sub(" ", source[pos:]))
    return "".join(out)


def analyze_js_source(path: str, source: str) -> JsModule:
    code = strip_comments(source)
    mod = JsModule(path=path)
    mod.imports = sorted({next(g for g in m.groups() if g) for m in _IMPORT_RE.finditer(code)})
    mod.exports = [{"kind": m.group(1), "name": m.group(2)} for m in _EXPORT_RE.finditer(code)]
    mod.routes = [
        {"method": m.group(1).upper(), "path": m.group(2), "file": path, "lineno": code[: m.start()].count("\n") + 1}
        for m in _EXPRESS_ROUTE_RE.finditer(code)
    ]
    # Next.js App Router: app/**/route.ts exports HTTP verbs.
    if re.search(r"(^|/)app/(.+/)?route\.(t|j)sx?$", path):
        route_path = "/" + "/".join(path.split("app/", 1)[1].split("/")[:-1])
        for e in mod.exports:
            if e["name"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                mod.routes.append({"method": e["name"], "path": route_path, "file": path, "lineno": 1})
    return mod


def load_tsconfig_paths(text: str | None) -> dict[str, list[str]]:
    if not text:
        return {}
    try:
        data = json.loads(re.sub(r",(\s*[}\]])", r"\1", strip_comments(text)))
    except json.JSONDecodeError:
        return {}
    opts = data.get("compilerOptions", {}) or {}
    base_url = opts.get("baseUrl", ".")
    return {
        alias: [posixpath.normpath(posixpath.join(base_url, t)) for t in targets]
        for alias, targets in (opts.get("paths", {}) or {}).items()
    }


def resolve_specifier(
    importer: str, spec: str, known_files: set[str], aliases: dict[str, list[str]] | None = None
) -> str | None:
    """Map an import specifier to a repository file, or None for external packages."""
    candidates: list[str] = []
    if spec.startswith("."):
        candidates.append(posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)))
    else:
        for alias, targets in (aliases or {}).items():
            prefix = alias.rstrip("*")
            if alias.endswith("*") and spec.startswith(prefix):
                rest = spec[len(prefix):]
                candidates.extend(t.rstrip("*") + rest for t in targets)
            elif spec == alias:
                candidates.extend(targets)
        if not candidates:
            return None
    for base in candidates:
        if base in known_files:
            return base
        stem = re.sub(r"\.(m|c)?js$", "", base)  # TS projects import "./x.js" meaning "./x.ts"
        for ext in JS_EXTENSIONS:
            if stem + ext in known_files:
                return stem + ext
        for ext in JS_EXTENSIONS:
            if f"{base}/index{ext}" in known_files:
                return f"{base}/index{ext}"
    return None

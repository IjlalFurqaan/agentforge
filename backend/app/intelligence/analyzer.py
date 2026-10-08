"""Repository analysis: the structured "understanding" every agent starts from."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.intelligence.dependencies import Dependency, extract_dependencies
from app.intelligence.graph import build_graphs
from app.intelligence.js_imports import JS_EXTENSIONS, analyze_js_source, load_tsconfig_paths, resolve_specifier
from app.intelligence.python_ast import analyze_python_source
from app.intelligence.scanner import PROGRAMMING_LANGUAGES, ScanResult, read_text, scan_repository

FRAMEWORK_SIGNATURES: dict[str, tuple[str, str]] = {
    # dependency name -> (framework, category)
    "fastapi": ("FastAPI", "web"), "django": ("Django", "web"), "flask": ("Flask", "web"),
    "starlette": ("Starlette", "web"), "express": ("Express", "web"), "next": ("Next.js", "web"),
    "react": ("React", "ui"), "vue": ("Vue", "ui"), "svelte": ("Svelte", "ui"), "@nestjs/core": ("NestJS", "web"),
    "sqlalchemy": ("SQLAlchemy", "orm"), "alembic": ("Alembic", "migrations"), "prisma": ("Prisma", "orm"),
    "@prisma/client": ("Prisma", "orm"), "typeorm": ("TypeORM", "orm"), "mongoose": ("Mongoose", "orm"),
    "pydantic": ("Pydantic", "validation"), "zod": ("Zod", "validation"), "celery": ("Celery", "queue"),
    "pytest": ("pytest", "testing"), "jest": ("Jest", "testing"), "vitest": ("Vitest", "testing"),
    "mocha": ("Mocha", "testing"), "playwright": ("Playwright", "testing"), "@playwright/test": ("Playwright", "testing"),
    "langgraph": ("LangGraph", "ai"), "langchain": ("LangChain", "ai"), "openai": ("OpenAI SDK", "ai"),
    "anthropic": ("Anthropic SDK", "ai"), "psycopg": ("PostgreSQL", "database"), "asyncpg": ("PostgreSQL", "database"),
    "psycopg2-binary": ("PostgreSQL", "database"), "pg": ("PostgreSQL", "database"), "redis": ("Redis", "database"),
    "pyjwt": ("PyJWT", "auth"), "python-jose": ("python-jose", "auth"), "jsonwebtoken": ("jsonwebtoken", "auth"),
    "passlib": ("passlib", "auth"), "authlib": ("Authlib", "auth"), "next-auth": ("NextAuth", "auth"),
}

MANIFESTS = {"pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Gemfile"}
_SECRET_FILE_RE = re.compile(r"(^|/)(\.env(\.[\w-]+)?|id_rsa|.*\.pem|.*\.p12|credentials\.json)$")


class TestingInfo(BaseModel):
    frameworks: list[str] = Field(default_factory=list)
    test_files: int = 0
    test_dirs: list[str] = Field(default_factory=list)
    install_commands: list[list[str]] = Field(default_factory=list)
    test_command: list[str] | None = None
    junit_supported: bool = False


class RepoAnalysis(BaseModel):
    root_name: str
    file_count: int
    total_lines: int
    truncated: bool
    languages: list[dict[str, Any]]
    primary_language: str | None
    dependencies: list[dict[str, Any]]
    frameworks: list[dict[str, str]]
    testing: TestingInfo
    infrastructure: dict[str, Any]
    entry_points: list[str]
    routes: list[dict[str, Any]]
    data_models: list[dict[str, Any]]
    symbols_summary: dict[str, int]
    module_graph: dict[str, Any]
    component_graph: dict[str, Any]
    hotspots: list[dict[str, Any]]
    tree: list[dict[str, Any]]
    observations: list[dict[str, str]]
    readme_excerpt: str | None = None

    def brief(self, max_chars: int = 6000) -> str:
        """Compact text summary used in agent prompts."""
        lines = [
            f"Repository '{self.root_name}': {self.file_count} files, {self.total_lines} lines; "
            f"primary language {self.primary_language}.",
            "Languages: " + ", ".join(f"{lang['language']} ({lang['files']} files)" for lang in self.languages[:6]),
            "Frameworks: " + (", ".join(f"{f['name']} [{f['category']}]" for f in self.frameworks) or "none detected"),
            "Components: " + ", ".join(f"{n['id']} ({n['files']} files)" for n in self.component_graph["nodes"][:15]),
            "Component dependencies: "
            + (", ".join(f"{e['source']}->{e['target']}" for e in self.component_graph["edges"][:25]) or "none"),
            f"Tests: {self.testing.test_files} test files, frameworks {self.testing.frameworks or 'none'}, "
            f"command {' '.join(self.testing.test_command) if self.testing.test_command else 'unknown'}",
            "Entry points: " + (", ".join(self.entry_points) or "unknown"),
        ]
        if self.routes:
            lines.append("HTTP routes: " + "; ".join(f"{r['method']} {r['path']} ({r['file']})" for r in self.routes[:30]))
        if self.data_models:
            lines.append("Data models: " + "; ".join(f"{m['name']} [{m['kind']}] in {m['file']}" for m in self.data_models[:30]))
        runtime = [d for d in self.dependencies if not d["dev"]]
        lines.append("Runtime dependencies: " + ", ".join(f"{d['name']}{d['spec']}" for d in runtime[:40]))
        if self.observations:
            lines.append("Observations: " + "; ".join(o["message"] for o in self.observations))
        lines.append("Directory tree:\n" + "\n".join(
            f"  {'  ' * t['depth']}{t['path'].rsplit('/', 1)[-1]}/ ({t['files']} files)" for t in self.tree[:60]))
        text = "\n".join(lines)
        return text[:max_chars]


def _languages(scan: ScanResult) -> list[dict[str, Any]]:
    files: Counter[str] = Counter()
    lines: Counter[str] = Counter()
    for f in scan.files:
        if f.language and not f.is_binary:
            files[f.language] += 1
            lines[f.language] += f.lines
    total = sum(lines[lang] for lang in lines if lang in PROGRAMMING_LANGUAGES) or 1
    return [
        {"language": lang, "files": files[lang], "lines": lines[lang],
         "share": round(lines[lang] / total, 4) if lang in PROGRAMMING_LANGUAGES else 0.0,
         "is_code": lang in PROGRAMMING_LANGUAGES}
        for lang, _ in lines.most_common()
    ]


def _tree(scan: ScanResult, max_depth: int = 3) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for f in scan.files:
        parts = f.path.split("/")[:-1]
        for depth in range(1, min(len(parts), max_depth) + 1):
            counts["/".join(parts[:depth])] += 1
    return [{"path": p, "depth": p.count("/"), "files": n} for p, n in sorted(counts.items())]


def _testing(root: Path, scan: ScanResult, deps: list[Dependency]) -> TestingInfo:
    paths = {f.path for f in scan.files}
    names = {d.name for d in deps}
    info = TestingInfo()
    py_tests = [p for p in paths if re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", p)]
    js_tests = [p for p in paths if re.search(r"\.(test|spec)\.(t|j)sx?$", p) or "/__tests__/" in f"/{p}"]
    info.test_files = len(py_tests) + len(js_tests)
    info.test_dirs = sorted({p.split("/")[0] for p in py_tests + js_tests if "/" in p})[:10]

    pyproject = read_text(root, "pyproject.toml") if "pyproject.toml" in paths else None
    has_pytest_cfg = "pytest.ini" in paths or "conftest.py" in paths or bool(pyproject and "[tool.pytest" in pyproject)
    if py_tests or "pytest" in names or has_pytest_cfg:
        info.frameworks.append("pytest")
        install: list[list[str]] = []
        req_files = sorted(p for p in paths if re.fullmatch(r"requirements([-_.\w]*)\.txt", p))
        if pyproject and "[project]" in pyproject:
            extras = []
            try:
                optional = tomllib.loads(pyproject).get("project", {}).get("optional-dependencies", {}) or {}
                extras = [e for e in ("dev", "test", "tests") if e in optional]
            except tomllib.TOMLDecodeError:
                pass
            install.append(["pip", "install", "-e", f".[{','.join(extras)}]" if extras else "."])
        for req in req_files:
            install.append(["pip", "install", "-r", req])
        if "pytest" not in names:
            install.append(["pip", "install", "pytest"])
        info.install_commands = install
        info.test_command = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "--junitxml=.agentforge/junit.xml"]
        info.junit_supported = True
    elif "package.json" in paths:
        pkg = read_text(root, "package.json") or ""
        lock = "package-lock.json" in paths
        if '"test"' in pkg:
            for fw in ("jest", "vitest", "mocha"):
                if fw in names:
                    info.frameworks.append(fw)
            info.install_commands = [["npm", "ci" if lock else "install", "--no-audit", "--no-fund"]]
            info.test_command = ["npm", "test", "--silent"]
    return info


def _observations(scan: ScanResult, testing: TestingInfo, deps: list[Dependency], infra: dict[str, Any]) -> list[dict[str, str]]:
    obs: list[dict[str, str]] = []
    paths = {f.path for f in scan.files}
    secret_like = sorted(p for p in paths if _SECRET_FILE_RE.search(p) and not p.endswith((".example", ".sample", ".template")))
    if secret_like:
        obs.append({"severity": "high", "message": f"Possible secrets committed: {', '.join(secret_like[:5])}"})
    if testing.test_files == 0:
        obs.append({"severity": "medium", "message": "No automated tests found"})
    if "package.json" in paths and not ({"package-lock.json", "yarn.lock", "pnpm-lock.yaml"} & paths):
        obs.append({"severity": "low", "message": "Node project without a lockfile"})
    unpinned = [d.name for d in deps if d.ecosystem == "pypi" and not d.dev and not d.spec]
    if unpinned:
        obs.append({"severity": "low", "message": f"Unpinned Python dependencies: {', '.join(unpinned[:8])}"})
    if not infra["dockerfiles"]:
        obs.append({"severity": "info", "message": "No Dockerfile present"})
    if not infra["ci"]:
        obs.append({"severity": "info", "message": "No CI configuration detected"})
    if scan.truncated:
        obs.append({"severity": "info", "message": "Repository scan truncated at the file limit"})
    return obs


def analyze_repository(root: Path, *, max_files: int = 20_000, max_file_bytes: int = 1_000_000) -> RepoAnalysis:
    root = root.resolve()
    scan = scan_repository(root, max_files=max_files, max_file_bytes=max_file_bytes)
    paths = {f.path for f in scan.files}
    meta = {f.path: {"language": f.language, "lines": f.lines} for f in scan.files}

    manifest_paths = [p for p in paths if p.rsplit("/", 1)[-1] in MANIFESTS
                      or re.fullmatch(r"requirements([-_.\w]*)\.txt", p.rsplit("/", 1)[-1])]
    deps = extract_dependencies(root, sorted(manifest_paths))
    dep_names = {d.name for d in deps}
    frameworks = sorted({FRAMEWORK_SIGNATURES[n] for n in dep_names if n in FRAMEWORK_SIGNATURES})

    # --- Python ---------------------------------------------------------------------------------
    py_files = [f.path for f in scan.files if f.language == "Python" and not f.is_binary]
    module_to_path: dict[str, str] = {}
    py_modules = []
    for p in py_files:
        src = read_text(root, p, max_file_bytes)
        if src is None:
            continue
        mod = analyze_python_source(p, src)
        py_modules.append(mod)
        module_to_path[mod.module] = p
    edges: dict[str, set[str]] = {}
    routes: list[dict[str, Any]] = []
    data_models: list[dict[str, Any]] = []
    symbol_counts: Counter[str] = Counter()
    entry_points: list[str] = []
    for mod in py_modules:
        targets = {module_to_path[i] for i in mod.imports if i in module_to_path and module_to_path[i] != mod.path}
        if targets:
            edges[mod.path] = targets
        routes.extend(vars(r) for r in mod.routes)
        for s in mod.symbols:
            symbol_counts[s.kind] += 1
            if s.category in {"pydantic_model", "orm_model"}:
                data_models.append({"name": s.name, "kind": s.category, "file": mod.path, "lineno": s.lineno})
        if mod.path.rsplit("/", 1)[-1] in {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py"}:
            entry_points.append(mod.path)

    # --- JavaScript / TypeScript ----------------------------------------------------------------
    aliases = load_tsconfig_paths(read_text(root, "tsconfig.json")) if "tsconfig.json" in paths else {}
    for p in sorted(x for x in paths if x.endswith(JS_EXTENSIONS) and not x.endswith(".d.ts")):
        src = read_text(root, p, max_file_bytes)
        if src is None:
            continue
        mod = analyze_js_source(p, src)
        targets = {t for spec in mod.imports if (t := resolve_specifier(p, spec, paths, aliases)) and t != p}
        if targets:
            edges[p] = targets
        routes.extend(mod.routes)
        symbol_counts["js_export"] += len(mod.exports)
        if re.search(r"(^|/)(index|server|main)\.(t|j)s$", p) and p.count("/") <= 1:
            entry_points.append(p)

    code_meta = {p: m for p, m in meta.items() if m["language"] in PROGRAMMING_LANGUAGES}
    module_graph, component_graph = build_graphs(edges, code_meta)
    hotspots = sorted(module_graph.nodes, key=lambda n: (-n["fan_in"], n["id"]))[:10]

    infra = {
        "dockerfiles": sorted(p for p in paths if p.rsplit("/", 1)[-1].startswith("Dockerfile")),
        "compose": sorted(p for p in paths if re.search(r"(^|/)(docker-)?compose(\.[\w-]+)?\.ya?ml$", p)),
        "ci": sorted(p for p in paths if p.startswith((".github/workflows/", ".gitlab-ci", ".circleci/"))),
        "env_examples": sorted(p for p in paths if re.search(r"\.env\.(example|sample|template)$", p)),
        "migrations": sorted({p.split("/versions/")[0] for p in paths if "/versions/" in p and p.endswith(".py")})[:5],
        "kubernetes": sorted(p for p in paths if p.startswith(("k8s/", "helm/", "charts/")))[:10],
    }
    testing = _testing(root, scan, deps)
    languages = _languages(scan)
    code_langs = [lang for lang in languages if lang["is_code"]]
    readme = next((p for p in ("README.md", "README.rst", "README.txt", "README") if p in paths), None)

    return RepoAnalysis(
        root_name=root.name,
        file_count=len(scan.files),
        total_lines=sum(f.lines for f in scan.files),
        truncated=scan.truncated,
        languages=languages,
        primary_language=code_langs[0]["language"] if code_langs else None,
        dependencies=[d.to_dict() for d in deps],
        frameworks=[{"name": n, "category": c} for n, c in frameworks],
        testing=testing,
        infrastructure=infra,
        entry_points=sorted(set(entry_points)),
        routes=routes[:200],
        data_models=data_models[:200],
        symbols_summary=dict(symbol_counts),
        module_graph=module_graph.to_dict(),
        component_graph=component_graph.to_dict(),
        hotspots=hotspots,
        tree=_tree(scan),
        observations=_observations(scan, testing, deps, infra),
        readme_excerpt=(read_text(root, readme) or "")[:3000] if readme else None,
    )

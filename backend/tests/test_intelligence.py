from __future__ import annotations

from app.intelligence.analyzer import analyze_repository
from app.intelligence.dependencies import parse_go_mod, parse_package_json, parse_pyproject, parse_requirements_txt
from app.intelligence.graph import strongly_connected_components
from app.intelligence.js_imports import analyze_js_source, load_tsconfig_paths, resolve_specifier
from app.intelligence.python_ast import analyze_python_source
from app.intelligence.scanner import scan_repository
from app.intelligence.search import CodeIndex, tokenize
from tests.conftest import DEMO_REPO


def test_analyze_demo_repository():
    a = analyze_repository(DEMO_REPO)
    assert a.primary_language == "Python"
    names = {f["name"] for f in a.frameworks}
    assert {"FastAPI", "SQLAlchemy", "pytest"} <= names
    routes = {(r["method"], r["path"]) for r in a.routes}
    assert ("POST", "/projects") in routes and ("PATCH", "/items/{item_id}") in routes
    models = {(m["name"], m["kind"]) for m in a.data_models}
    assert ("Project", "orm_model") in models and ("ProjectCreate", "pydantic_model") in models
    assert a.testing.test_command and a.testing.test_command[:3] == ["python", "-m", "pytest"]
    assert a.testing.test_files == 1
    # internal import graph: routers depend on models, models depend on db
    edges = {(e["source"], e["target"]) for e in a.module_graph["edges"]}
    assert ("app/routers/projects.py", "app/models.py") in edges
    assert ("app/models.py", "app/db.py") in edges
    assert "app/main.py" in a.entry_points
    assert "FastAPI" in a.brief()


def test_python_ast_relative_imports_and_routes():
    src = (
        "from . import models\nfrom ..core.config import settings\nimport os\n"
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/users/{uid}')\nasync def get_user(uid: int) -> dict:\n    '''Fetch.'''\n    return {}\n"
        "class User(Base):\n    __tablename__ = 'users'\n"
        "class UserIn(BaseModel):\n    name: str\n"
    )
    mod = analyze_python_source("app/api/users.py", src)
    assert "app.api.models" in mod.imports and "app.core.config" in mod.imports and "os" in mod.imports
    assert mod.routes[0].method == "GET" and mod.routes[0].path == "/users/{uid}"
    cats = {s.name: s.category for s in mod.symbols}
    assert cats["User"] == "orm_model" and cats["UserIn"] == "pydantic_model"


def test_js_imports_and_alias_resolution():
    src = ("import x from './util'\nimport type { T } from \"../types\"\n// import fake from './commented'\n"
           "const y = require('lodash')\nexport async function handler() {}\nconst s = 'http://not-a-comment'\n")
    mod = analyze_js_source("src/app/page.tsx", src)
    assert mod.imports == ["../types", "./util", "lodash"]
    assert mod.exports == [{"kind": "function", "name": "handler"}]
    files = {"src/app/util.ts", "src/types/index.ts", "src/lib/api.ts"}
    assert resolve_specifier("src/app/page.tsx", "./util", files) == "src/app/util.ts"
    assert resolve_specifier("src/app/page.tsx", "../types", files) == "src/types/index.ts"
    assert resolve_specifier("src/app/page.tsx", "lodash", files) is None
    aliases = load_tsconfig_paths('{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}, // c\n}')
    assert resolve_specifier("src/app/page.tsx", "@/lib/api", files, aliases) == "src/lib/api.ts"


def test_dependency_parsers():
    reqs = parse_requirements_txt("fastapi>=0.110  # web\n-r base.txt\nPyJWT[crypto]==2.8.0\n\n", "requirements.txt")
    assert [(d.name, d.spec) for d in reqs] == [("fastapi", ">=0.110"), ("pyjwt", "==2.8.0")]
    py = parse_pyproject('[project]\ndependencies=["sqlalchemy>=2"]\n[project.optional-dependencies]\ndev=["pytest"]\n',
                         "pyproject.toml")
    assert {(d.name, d.dev) for d in py} == {("sqlalchemy", False), ("pytest", True)}
    npm = parse_package_json('{"dependencies":{"next":"15.0.0"},"devDependencies":{"jest":"^29"}}', "package.json")
    assert {(d.name, d.dev) for d in npm} == {("next", False), ("jest", True)}
    go = parse_go_mod("module x\nrequire (\n  github.com/gin-gonic/gin v1.9.1\n  golang.org/x/net v0.1.0 // indirect\n)\n",
                      "go.mod")
    assert [(d.name, d.dev) for d in go] == [("github.com/gin-gonic/gin", False), ("golang.org/x/net", True)]


def test_cycle_detection():
    cycles = strongly_connected_components({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": {"e"}})
    assert ["a", "b", "c"] in cycles and ["e"] in cycles and all("d" not in c for c in cycles)


def test_code_search_ranks_symbols():
    assert "project" in tokenize("createProject") and "create" in tokenize("create_project")
    index = CodeIndex.build(DEMO_REPO, scan_repository(DEMO_REPO))
    hits = index.search("create project duplicate name conflict")
    top = {h.symbol for h in hits[:3]}
    # both the implementation and the test that exercises it rank at the top
    assert {"create_project", "test_duplicate_project_name_conflicts"} <= top

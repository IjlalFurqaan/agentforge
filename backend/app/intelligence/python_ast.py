"""Python source analysis with the standard-library AST."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import Any

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route"}


@dataclass
class Symbol:
    name: str
    kind: str  # function | async_function | class | method
    lineno: int
    end_lineno: int
    signature: str = ""
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    doc: str = ""
    category: str | None = None  # pydantic_model | orm_model | exception | test | None


@dataclass
class Route:
    method: str
    path: str
    handler: str
    file: str
    lineno: int


@dataclass
class PythonModule:
    path: str
    module: str
    imports: list[str] = field(default_factory=list)  # absolute module names
    symbols: list[Symbol] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    syntax_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def module_name_for(path: str) -> str:
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    return ast.unparse(node) if hasattr(ast, "unparse") else "?"


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    returns = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    return f"({args}){returns}"


def _categorise_class(bases: list[str], body: list[ast.stmt], name: str) -> str | None:
    short = {b.rsplit(".", 1)[-1] for b in bases}
    if short & {"BaseModel", "BaseSettings"}:
        return "pydantic_model"
    if short & {"Base", "DeclarativeBase", "Model", "SQLModel"} or any(
        isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets)
        for s in body
    ):
        return "orm_model"
    if any(b.endswith(("Exception", "Error")) for b in short):
        return "exception"
    if name.startswith("Test"):
        return "test"
    return None


def _route_from_decorator(dec: ast.expr, handler: str, file: str) -> Route | None:
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr.lower()
    if method not in HTTP_METHODS and method != "route":
        return None
    path = ""
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        path = dec.args[0].value
    elif method in HTTP_METHODS:
        return None
    if method == "route":  # Flask
        methods = ["GET"]
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
        method = ",".join(m.upper() for m in methods)
    return Route(method=method.upper(), path=path, handler=handler, file=file, lineno=dec.lineno)


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    parts = module.split(".") if module else []
    base = parts if is_package else parts[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)] if level - 1 <= len(base) else []
    return ".".join([*base, target] if target else base)


def analyze_python_source(path: str, source: str) -> PythonModule:
    mod = PythonModule(path=path, module=module_name_for(path))
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        mod.syntax_error = f"{type(exc).__name__}: {exc}"
        return mod

    is_package = path.endswith("__init__.py")
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(mod.module, is_package, node.level, node.module)
            else:
                base = node.module or ""
            if base:
                imports.add(base)
                # `from pkg import submodule` may refer to a module; record candidates too.
                imports.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    mod.imports = sorted(imports)

    # Routes can be declared at any depth (e.g. inside an application factory).
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if route := _route_from_decorator(dec, node.name, path):
                    mod.routes.append(route)
    mod.routes.sort(key=lambda r: r.lineno)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = [_dotted(d) for d in node.decorator_list]
            mod.symbols.append(Symbol(
                name=node.name,
                kind="async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                lineno=node.lineno, end_lineno=node.end_lineno or node.lineno,
                signature=_signature(node), decorators=decorators,
                doc=(ast.get_docstring(node) or "").split("\n", 1)[0][:200],
                category="test" if node.name.startswith("test_") else None,
            ))
        elif isinstance(node, ast.ClassDef):
            bases = [_dotted(b) for b in node.bases]
            mod.symbols.append(Symbol(
                name=node.name, kind="class", lineno=node.lineno, end_lineno=node.end_lineno or node.lineno,
                decorators=[_dotted(d) for d in node.decorator_list], bases=bases,
                doc=(ast.get_docstring(node) or "").split("\n", 1)[0][:200],
                category=_categorise_class(bases, node.body, node.name),
            ))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mod.symbols.append(Symbol(
                        name=f"{node.name}.{item.name}", kind="method", lineno=item.lineno,
                        end_lineno=item.end_lineno or item.lineno, signature=_signature(item),
                        decorators=[_dotted(d) for d in item.decorator_list],
                        category="test" if item.name.startswith("test_") else None,
                    ))
    return mod

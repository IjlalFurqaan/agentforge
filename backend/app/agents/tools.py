"""Tools exposed to agents. Each agent gets only the tools its role needs."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agents.runtime import AgentRuntime
from app.llm.types import ToolSpec

Handler = Callable[[dict[str, Any]], Awaitable[str]]
MAX_RESULT_CHARS = 14_000


@dataclass
class Tool:
    spec: ToolSpec
    handler: Handler
    mutating: bool = False

    def summarize_args(self, args: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 300:
                out[k] = f"<{len(v)} chars>"
            else:
                out[k] = v
        return out


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _clip(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n... [output truncated, {len(text) - MAX_RESULT_CHARS} more chars]"


def read_tools(rt: AgentRuntime) -> list[Tool]:
    ws = rt.workspace

    async def list_dir(a: dict[str, Any]) -> str:
        return "\n".join(ws.list_dir(a.get("path") or "."))

    async def read_file(a: dict[str, Any]) -> str:
        return _clip(ws.read_file(a["path"], a.get("start_line"), a.get("end_line"), max_chars=MAX_RESULT_CHARS))

    async def search_code(a: dict[str, Any]) -> str:
        hits = ws.index().search(a["query"], k=int(a.get("limit") or 8))
        if not hits:
            return "No matches."
        return _clip("\n\n".join(
            f"## {h.path}:{h.start}-{h.end}" + (f" ({h.symbol})" if h.symbol else "") + f"\n{h.snippet}" for h in hits))

    async def grep(a: dict[str, Any]) -> str:
        hits = ws.grep(a["pattern"], a.get("glob"))
        return _clip("\n".join(hits) if hits else "No matches.")

    return [
        Tool(ToolSpec(name="list_dir", description="List a directory in the repository (relative path).",
                      parameters=_obj({"path": {"type": "string", "description": "Directory, e.g. '.' or 'app/api'"}}, ["path"])),
             list_dir),
        Tool(ToolSpec(name="read_file",
                      description="Read a text file with line numbers. Use start_line/end_line for large files.",
                      parameters=_obj({"path": {"type": "string"}, "start_line": {"type": "integer"},
                                       "end_line": {"type": "integer"}}, ["path"])), read_file),
        Tool(ToolSpec(name="search_code",
                      description="Ranked code search over functions, classes and files (BM25 over identifiers).",
                      parameters=_obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"])), search_code),
        Tool(ToolSpec(name="grep", description="Regex search across text files. Returns path:line: text.",
                      parameters=_obj({"pattern": {"type": "string"}, "glob": {"type": "string",
                                       "description": "Optional fnmatch filter, e.g. 'app/*.py'"}}, ["pattern"])), grep),
    ]


def write_tools(rt: AgentRuntime, *, test_only: bool = False) -> list[Tool]:
    ws = rt.workspace
    scope = " Only test files (tests/, test_*.py, *.test.ts, ...) may be written." if test_only else ""

    async def write_file(a: dict[str, Any]) -> str:
        result = ws.write_file(a["path"], a["content"], test_only=test_only)
        return f"{result} {a['path']} ({len(a['content'])} chars)"

    async def edit_file(a: dict[str, Any]) -> str:
        ws.edit_file(a["path"], a["old_text"], a["new_text"], test_only=test_only)
        return f"edited {a['path']}"

    tools = [
        Tool(ToolSpec(name="write_file", description="Create or overwrite a file with the full content." + scope,
                      parameters=_obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])),
             write_file, mutating=True),
        Tool(ToolSpec(name="edit_file", description="Replace exactly one occurrence of old_text with new_text. "
                      "Prefer this over write_file for small changes to existing files." + scope,
                      parameters=_obj({"path": {"type": "string"}, "old_text": {"type": "string"},
                                       "new_text": {"type": "string"}}, ["path", "old_text", "new_text"])),
             edit_file, mutating=True),
    ]
    if not test_only:
        async def delete_file(a: dict[str, Any]) -> str:
            ws.delete_file(a["path"])
            return f"deleted {a['path']}"

        tools.append(Tool(ToolSpec(name="delete_file", description="Delete a file.",
                                   parameters=_obj({"path": {"type": "string"}}, ["path"])), delete_file, mutating=True))
    return tools


def exec_tool(rt: AgentRuntime, agent: str) -> Tool:
    async def run_command(a: dict[str, Any]) -> str:
        argv = a["argv"]
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise ValueError("argv must be a list of strings")
        result = await rt.execute(argv, agent=agent, workdir=a.get("workdir") or ".", timeout_s=min(
            int(a.get("timeout_seconds") or rt.settings.sandbox_timeout_seconds), rt.settings.sandbox_timeout_seconds))
        status = "TIMED OUT" if result.timed_out else f"exit code {result.exit_code}"
        network = "on" if result.network else "off"
        return _clip(f"[{status}, {result.duration_ms} ms, sandbox={result.backend}, network={network}]\n"
                     f"--- stdout ---\n{result.stdout[-8000:]}\n--- stderr ---\n{result.stderr[-4000:]}")

    return Tool(ToolSpec(
        name="run_command",
        description=("Run a command in the isolated sandbox (no network, no shell). argv is an exec list such as "
                     '["python", "-m", "pytest", "-q", "tests/test_auth.py"]. Shell syntax (pipes, &&, >) is not '
                     "supported. Dependencies are already installed in the sandbox's virtualenv."),
        parameters=_obj({"argv": {"type": "array", "items": {"type": "string"}}, "workdir": {"type": "string"},
                         "timeout_seconds": {"type": "integer"}}, ["argv"])), run_command)


def submit_tool(schema: dict[str, Any], description: str) -> ToolSpec:
    return ToolSpec(name="submit", description=description, parameters=schema)


def format_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)[:500]

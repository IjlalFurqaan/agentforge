from __future__ import annotations

from typing import Any

from app.agents.base import as_json, clip_diff, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import CodeReview
from app.agents.tools import read_tools

NAME = "reviewer"


async def run(rt: AgentRuntime, *, requirements: dict[str, Any], design: dict[str, Any], implementation: dict[str, Any],
              diff: str, tests: dict[str, Any], security: dict[str, Any]) -> CodeReview:
    prompt = (
        "Review this pull request independently. Use the tools to read full files where the diff lacks context, "
        "then call submit."
        + section("Requirements", as_json(requirements, 8000))
        + section("Approved design", as_json(design, 10_000))
        + section("Developer's summary and decisions", as_json(implementation, 5000))
        + section("Test results (ground truth from the sandbox)", as_json(tests, 6000))
        + section("Security review summary", as_json(security, 6000))
        + section("Diff", clip_diff(diff, 50_000))
    )
    return await run_tool_loop(rt, agent=NAME, system=system_prompt(NAME), prompt=prompt, tools=read_tools(rt),
                               output=CodeReview, max_tool_calls=min(rt.limits.max_tool_calls, 15),
                               submit_description="Submit the code review.")

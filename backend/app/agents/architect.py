from __future__ import annotations

from typing import Any

from app.agents.base import as_json, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import TechnicalDesign
from app.agents.tools import read_tools

NAME = "architect"


async def run(rt: AgentRuntime, *, request: str, requirements: dict[str, Any], repo_brief: str,
              previous_design: dict[str, Any] | None, feedback: list[str]) -> TechnicalDesign:
    prompt = (
        "Design the implementation for this change. Inspect the repository with the tools before deciding, "
        "then call submit with the technical design."
        + section("Request", request)
        + section("Requirements specification (from the Product Manager)", as_json(requirements))
        + section("Repository overview (static analysis)", repo_brief)
    )
    if previous_design:
        prompt += section("Previous design (revise it)", as_json(previous_design, 15_000))
    if feedback:
        prompt += section("Feedback you must address", "\n".join(f"- {f}" for f in feedback))
    return await run_tool_loop(rt, agent=NAME, system=system_prompt(NAME), prompt=prompt, tools=read_tools(rt),
                               output=TechnicalDesign, max_tool_calls=min(rt.limits.max_tool_calls, 25),
                               submit_description="Submit the technical design.")

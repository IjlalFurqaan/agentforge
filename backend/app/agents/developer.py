from __future__ import annotations

from typing import Any

from app.agents.base import as_json, section, system_prompt
from app.agents.loop import run_tool_loop
from app.agents.runtime import AgentRuntime
from app.agents.schemas import ImplementationSummary
from app.agents.tools import exec_tool, read_tools, write_tools

NAME = "developer"


async def run(rt: AgentRuntime, *, request: str, requirements: dict[str, Any], design: dict[str, Any],
              feedback: list[dict[str, Any]], previous: dict[str, Any] | None, diff_stat: str,
              environment: dict[str, Any]) -> ImplementationSummary:
    iteration = rt.events.iteration
    prompt = (
        f"Implementation iteration {iteration}. Implement the design in the repository using the tools, "
        "then call submit."
        + section("Request", request)
        + section("Requirements and acceptance criteria", as_json(requirements, 12_000))
        + section("Approved technical design", as_json(design, 15_000))
        + section("Sandbox environment", as_json(environment, 3000))
    )
    if previous:
        prompt += section("Your previous iteration summary", as_json(previous, 6000))
        prompt += section("Current changes vs. base (git diff --stat)", diff_stat or "(no changes)")
    if feedback:
        prompt += section(
            "Open feedback addressed to you — respond to EVERY item by message_id in feedback_responses",
            "\n\n".join(f"[message_id={m['id']}] from {m['from']} ({m['kind']}):\n{m['content']}" for m in feedback),
        )
    tools = [*read_tools(rt), *write_tools(rt), exec_tool(rt, NAME)]
    return await run_tool_loop(rt, agent=NAME, system=system_prompt(NAME), prompt=prompt, tools=tools,
                               output=ImplementationSummary,
                               submit_description="Submit the implementation summary once all changes are written.")

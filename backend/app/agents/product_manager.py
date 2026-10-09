from __future__ import annotations

from typing import Any

from app.agents.base import as_json, section, system_prompt
from app.agents.runtime import AgentRuntime
from app.agents.schemas import RequirementsSpec
from app.llm.types import Message

NAME = "product_manager"


async def run(rt: AgentRuntime, *, request: str, repo_brief: str, readme: str | None,
              clarifications: list[dict[str, Any]], previous: dict[str, Any] | None) -> RequirementsSpec:
    prompt = (
        "Analyse this engineering request and produce the requirements specification."
        + section("Request", request)
        + section("Repository overview (from static analysis)", repo_brief)
        + section("README excerpt", (readme or "")[:3000])
    )
    if clarifications:
        prompt += section("Answers from the human stakeholder (authoritative)", as_json(clarifications))
    if previous:
        prompt += section("Your previous specification (update it with the answers above)", as_json(previous, 12_000))
    return await rt.llm.structured(NAME, system_prompt(NAME), [Message.user(prompt)], RequirementsSpec)

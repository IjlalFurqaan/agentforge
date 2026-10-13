"""Provider-agnostic tool-use loop that ends when the agent calls `submit` with a valid result."""

from __future__ import annotations

import time

from pydantic import BaseModel, ValidationError

from app.agents.runtime import AgentRuntime
from app.agents.tools import Tool, submit_tool
from app.llm.schema import strict_json_schema
from app.llm.types import LLMRequest, Message, ToolResult
from app.sandbox import PolicyViolation, SandboxUnavailable
from app.services.workspace import WorkspaceError

RECOVERABLE_TOOL_ERRORS = (WorkspaceError, PolicyViolation, SandboxUnavailable, KeyError, ValueError, TypeError,
                           FileNotFoundError, IsADirectoryError, UnicodeDecodeError)


async def run_tool_loop[T: BaseModel](
    rt: AgentRuntime,
    *,
    agent: str,
    system: str,
    prompt: str,
    tools: list[Tool],
    output: type[T],
    max_tool_calls: int | None = None,
    submit_description: str = "Submit your final result. Call this exactly once when your work is complete.",
) -> T:
    budget = max_tool_calls or rt.limits.max_tool_calls
    by_name = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools] + [submit_tool(strict_json_schema(output), submit_description)]
    messages: list[Message] = [Message.user(prompt)]
    transcript: list[str] = []  # condensed log used if the loop has to be finalised without `submit`
    calls_made = 0
    nudges = 0

    while calls_made < budget:
        rt.check_cancelled()
        response = await rt.llm.generate(agent, LLMRequest(system=system, messages=messages, tools=specs,
                                                           max_tokens=rt.settings.llm_max_tokens))
        messages.append(response.as_message())
        if response.text:
            transcript.append(f"[{agent}] {response.text[:1500]}")
        calls = response.tool_calls
        if not calls:
            nudges += 1
            if nudges > 3:
                break
            hint = ("Your reply was cut off by the output limit; continue with smaller steps."
                    if response.stop_reason == "max_tokens" else
                    "Continue working with the tools, or call `submit` with your final result if you are done.")
            messages.append(Message.user(hint))
            continue

        results: list[ToolResult] = []
        submitted: T | None = None
        for call in calls:
            calls_made += 1
            if call.name == "submit":
                try:
                    submitted = output.model_validate(call.arguments)
                    results.append(ToolResult(tool_call_id=call.id, name=call.name, content="Submitted."))
                except ValidationError as exc:
                    results.append(ToolResult(tool_call_id=call.id, name=call.name, is_error=True,
                                              content=f"Invalid submission, fix and resubmit:\n{str(exc)[:3000]}"))
                continue
            tool = by_name.get(call.name)
            started = time.perf_counter()
            if tool is None:
                content, is_error = f"Unknown tool '{call.name}'. Available: {', '.join(by_name)} and submit.", True
            else:
                try:
                    content, is_error = await tool.handler(call.arguments), False
                except RECOVERABLE_TOOL_ERRORS as exc:
                    content, is_error = f"{type(exc).__name__}: {exc}", True
            duration = int((time.perf_counter() - started) * 1000)
            results.append(ToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error))
            args = tool.summarize_args(call.arguments) if tool else call.arguments
            transcript.append(f"[tool {call.name}] {args} -> {'ERROR ' if is_error else ''}{content[:600]}")
            await rt.emit("tool_call", f"{call.name}({_short(args)}){' failed' if is_error else ''}", agent=agent,
                          payload={"tool": call.name, "arguments": args, "is_error": is_error,
                                   "duration_ms": duration, "result_preview": content[:1200],
                                   "mutating": bool(tool and tool.mutating)})
        messages.append(Message(role="user", content=results))
        if submitted is not None:
            return submitted

    # Out of tool budget (or the model stopped using tools): finalise from a condensed transcript.
    await rt.emit("limit_reached", f"{agent} used its tool budget ({calls_made} calls); finalising",
                  agent=agent, payload={"tool_calls": calls_made, "budget": budget})
    condensed = "\n".join(transcript)[-30_000:]
    return await rt.llm.structured(agent, system, [Message.user(
        f"{prompt}\n\n# Work log so far\n{condensed}\n\nYour tool budget is exhausted. "
        "Produce your final result now, based only on the work actually performed.")], output)


def _short(args: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text[:160]

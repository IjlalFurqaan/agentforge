"""Anthropic Messages API provider (official `anthropic` SDK)."""

from __future__ import annotations

import time
from typing import Any

import anthropic

from app.core.config import Settings
from app.llm.providers.base import LLMProvider
from app.llm.types import (
    LLMError,
    LLMRefusal,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    Message,
    ModelConfig,
    TextBlock,
    ToolCall,
    ToolResult,
    Usage,
)

# Models that accept the server-side refusal fallback ("default" routing mode).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # With no explicit key the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant` profiles.
        kwargs: dict[str, Any] = {"timeout": settings.llm_request_timeout_seconds,
                                  "max_retries": settings.llm_max_retries}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        self.client = anthropic.AsyncAnthropic(**kwargs)

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "assistant" and m.provider == "anthropic" and m.provider_raw:
                out.append({"role": "assistant", "content": m.provider_raw})  # replay thinking blocks verbatim
                continue
            blocks: list[dict[str, Any]] = []
            for b in m.content:
                if isinstance(b, TextBlock):
                    if b.text:
                        blocks.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolCall):
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.arguments})
                elif isinstance(b, ToolResult):
                    blocks.append({"type": "tool_result", "tool_use_id": b.tool_call_id,
                                   "content": b.content or "(no output)", "is_error": b.is_error})
            out.append({"role": m.role, "content": blocks or [{"type": "text", "text": "(empty)"}]})
        return out

    async def generate(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        output_config: dict[str, Any] = {"effort": config.effort or self.settings.anthropic_effort}
        if request.response_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.response_schema}
        params: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens or request.max_tokens,
            "system": request.system,
            "messages": self._messages(request.messages),
            "output_config": output_config,
            "cache_control": {"type": "ephemeral"},  # agent loops resend a growing prefix
        }
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools
            ]
            params["tool_choice"] = {"type": "auto"}

        started = time.perf_counter()
        try:
            if self.settings.anthropic_refusal_fallbacks == "default" and config.model in _FALLBACK_MODELS:
                resp = await self.client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **params)
            else:
                resp = await self.client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError,
                anthropic.InternalServerError) as exc:
            raise LLMTransientError(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        latency = int((time.perf_counter() - started) * 1000)

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusal(f"Model declined the request (category={category})")

        content: list[Any] = []
        for block in resp.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(
            content=content, stop_reason=resp.stop_reason or "end_turn", usage=usage, provider=self.name,
            model=resp.model, latency_ms=latency,
            raw_assistant=[b.model_dump(exclude_none=True) for b in resp.content],
        )

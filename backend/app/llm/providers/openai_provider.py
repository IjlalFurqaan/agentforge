"""OpenAI Chat Completions provider. Also serves OpenAI-compatible servers (vLLM, LM Studio) via base_url."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import openai

from app.core.config import Settings
from app.llm.providers.base import LLMProvider, strip_reasoning
from app.llm.types import (
    LLMError,
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


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key or "unset",
            base_url=settings.openai_base_url,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            texts = [b.text for b in m.content if isinstance(b, TextBlock)]
            calls = [b for b in m.content if isinstance(b, ToolCall)]
            results = [b for b in m.content if isinstance(b, ToolResult)]
            if m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
                if calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls]
                out.append(msg)
            else:
                for r in results:
                    out.append({"role": "tool", "tool_call_id": r.tool_call_id,
                                "content": ("ERROR: " if r.is_error else "") + r.content})
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    async def generate(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        params: dict[str, Any] = {
            "model": config.model,
            "messages": self._messages(request.system, request.messages),
            "max_completion_tokens": config.max_tokens or request.max_tokens,
        }
        if request.tools:
            params["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in request.tools]
        if request.response_schema is not None:
            params["response_format"] = {"type": "json_schema", "json_schema": {
                "name": request.schema_name, "schema": request.response_schema, "strict": True}}
        if config.effort in {"low", "medium", "high"}:
            params["reasoning_effort"] = config.effort
        started = time.perf_counter()
        try:
            resp = await self.client.chat.completions.create(**params)
        except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
                openai.InternalServerError) as exc:
            raise LLMTransientError(f"{type(exc).__name__}: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        latency = int((time.perf_counter() - started) * 1000)
        choice = resp.choices[0]
        content: list[Any] = []
        text = strip_reasoning(choice.message.content or "")
        if text:
            content.append(TextBlock(text=text))
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__invalid_json__": tc.function.arguments}
            content.append(ToolCall(id=tc.id or uuid.uuid4().hex, name=tc.function.name, arguments=args))
        usage = Usage(input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
                      output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0)
        details = getattr(resp.usage, "prompt_tokens_details", None)
        if details is not None and getattr(details, "cached_tokens", None):
            usage.cache_read_tokens = details.cached_tokens
            usage.input_tokens -= details.cached_tokens
        stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(
            choice.finish_reason or "stop", choice.finish_reason or "end_turn")
        return LLMResponse(content=content, stop_reason=stop, usage=usage, provider=self.name,
                           model=resp.model or config.model, latency_ms=latency)

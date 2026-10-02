"""Ollama native /api/chat provider (local models).

The native API is used instead of Ollama's OpenAI-compatible endpoint because it lets us set the
context window (`num_ctx`) per request — the server default is far too small for agent loops and
silently truncates prompts — and pass a JSON schema via `format` for structured output.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

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


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.ollama_base_url.rstrip("/"),
                                        timeout=settings.llm_request_timeout_seconds, transport=transport)

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            texts = [b.text for b in m.content if isinstance(b, TextBlock)]
            calls = [b for b in m.content if isinstance(b, ToolCall)]
            results = [b for b in m.content if isinstance(b, ToolResult)]
            if m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
                if calls:
                    msg["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls]
                out.append(msg)
            else:
                for r in results:
                    out.append({"role": "tool", "tool_name": r.name,
                                "content": ("ERROR: " if r.is_error else "") + r.content})
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    async def generate(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        body: dict[str, Any] = {
            "model": config.model,
            "messages": self._messages(request.system, request.messages),
            "stream": False,
            "think": False,
            "options": {"num_ctx": config.context_window or 32768,
                        "num_predict": config.max_tokens or request.max_tokens},
        }
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in request.tools]
        if request.response_schema is not None:
            body["format"] = request.response_schema
        started = time.perf_counter()
        try:
            resp = await self.client.post("/api/chat", json=body)
        except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
            raise LLMError(f"Invalid OLLAMA_BASE_URL {self.settings.ollama_base_url!r}: {exc}") from exc
        except httpx.TransportError as exc:
            raise LLMTransientError(f"Ollama unreachable at {self.settings.ollama_base_url}: {exc}") from exc
        if resp.status_code >= 500:
            raise LLMTransientError(f"Ollama {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise LLMError(f"Ollama {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        latency = int((time.perf_counter() - started) * 1000)
        msg = data.get("message", {})
        content: list[Any] = []
        text = strip_reasoning(msg.get("content", ""))
        if text:
            content.append(TextBlock(text=text))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"__invalid_json__": args}
            content.append(ToolCall(id=tc.get("id") or f"call_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""),
                                    arguments=args))
        stop = "tool_use" if any(isinstance(b, ToolCall) for b in content) else (
            "max_tokens" if data.get("done_reason") == "length" else "end_turn")
        usage = Usage(input_tokens=data.get("prompt_eval_count", 0) or 0, output_tokens=data.get("eval_count", 0) or 0)
        return LLMResponse(content=content, stop_reason=stop, usage=usage, provider=self.name,
                           model=config.model, latency_ms=latency)

    async def list_models(self) -> list[str]:
        try:
            resp = await self.client.get("/api/tags", timeout=5)
            return sorted(m["name"] for m in resp.json().get("models", []))
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    async def preflight(self, model: str) -> None:
        try:
            resp = await self.client.get("/api/tags", timeout=10)
            resp.raise_for_status()
            names = {m["name"] for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMError(f"Ollama is not reachable at {self.settings.ollama_base_url!r}: {exc}") from exc
        if model not in names and f"{model}:latest" not in names:
            raise LLMError(f"Model '{model}' is not available on Ollama at {self.settings.ollama_base_url} "
                           f"(available: {', '.join(sorted(names)) or 'none'})")

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        resp = await self.client.post("/api/embed", json={"model": model, "input": texts})
        resp.raise_for_status()
        return resp.json()["embeddings"]

    async def aclose(self) -> None:
        await self.client.aclose()

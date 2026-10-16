from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.agents import schemas as agent_schemas
from app.llm.gateway import LLMGateway, ProviderRegistry, extract_json, resolve_model
from app.llm.pricing import cost_usd
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.base import LLMProvider
from app.llm.providers.ollama_provider import OllamaProvider
from app.llm.providers.openai_provider import OpenAIProvider
from app.llm.schema import strict_json_schema
from app.llm.types import (
    BudgetExceeded,
    LLMOutputError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    Message,
    ModelConfig,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("model", [agent_schemas.RequirementsSpec, agent_schemas.TechnicalDesign,
                                   agent_schemas.ImplementationSummary, agent_schemas.QAAnalysis,
                                   agent_schemas.SecurityReview, agent_schemas.CodeReview,
                                   agent_schemas.DeploymentReview, agent_schemas.QAWork])
def test_agent_schemas_are_strict(model):
    schema = strict_json_schema(model)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    for path, node in _walk(schema):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False, path
            assert set(node["required"]) == set(node.get("properties", {})), path


def test_model_resolution_priority(settings):
    settings.agent_models = {"developer": {"provider": "anthropic", "model": "claude-opus-5-5"}}
    assert resolve_model("developer", None, settings).model == "claude-opus-5-5"
    assert resolve_model("qa", None, settings).model == settings.default_llm_model
    task = {"default": {"provider": "ollama", "model": "qwen3:8b"}, "qa": {"provider": "openai", "model": "gpt-x"}}
    assert resolve_model("qa", task, settings).label == "openai/gpt-x"
    assert resolve_model("developer", task, settings).label == "ollama/qwen3:8b"


def test_pricing():
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert cost_usd("anthropic", "claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0 + 0.20)
    assert cost_usd("ollama", "qwen3:8b", usage) == 0.0
    assert cost_usd("openai", "unknown-model", usage) is None


def test_extract_json():
    assert json.loads(extract_json('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(extract_json('Sure! {"a": {"b": 2}} done')) == {"a": {"b": 2}}


class FlakyProvider(LLMProvider):
    name = "flaky"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def generate(self, request, config):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(content=[TextBlock(text=reply)], stop_reason="end_turn",
                           usage=Usage(input_tokens=10, output_tokens=5), provider="flaky", model="m")


class Out(BaseModel):
    answer: int


async def test_structured_retries_on_invalid_output(settings, monkeypatch):
    monkeypatch.setattr("app.llm.gateway.asyncio.sleep", _no_sleep)
    registry = ProviderRegistry(settings)
    provider = FlakyProvider([LLMTransientError("overloaded"), "not json", '{"answer": "x"}', '{"answer": 42}'])
    registry.register(provider)
    gw = LLMGateway(settings, registry, task_models={"default": {"provider": "flaky", "model": "m"}})
    out = await gw.structured("qa", "sys", [Message.user("q")], Out)
    assert out.answer == 42 and provider.calls == 4

    provider.replies = ["nope", "nope", "nope"]
    with pytest.raises(LLMOutputError):
        await gw.structured("qa", "sys", [Message.user("q")], Out)


async def _no_sleep(_):
    return None


async def test_budget_enforced(settings):
    registry = ProviderRegistry(settings)
    registry.register(FlakyProvider(['{"answer": 1}']))
    gw = LLMGateway(settings, registry, task_models={"default": {"provider": "flaky", "model": "m"}},
                    max_cost_usd=1.0, spent_usd=1.0)
    with pytest.raises(BudgetExceeded):
        await gw.generate("qa", LLMRequest(system="s", messages=[Message.user("q")]))


def test_anthropic_message_conversion_replays_raw_blocks(settings):
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
    msgs = AnthropicProvider._messages([
        Message.user("hi"),
        Message(role="assistant", content=[ToolCall(id="t1", name="x")], provider="anthropic", provider_raw=raw),
        Message(role="user", content=[ToolResult(tool_call_id="t1", content="ok")]),
    ])
    assert msgs[1]["content"] == raw  # thinking blocks with signatures are replayed verbatim
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}


def test_openai_message_conversion():
    msgs = OpenAIProvider._messages("sys", [
        Message.user("hi"),
        Message(role="assistant", content=[TextBlock(text="calling"), ToolCall(id="c1", name="f", arguments={"a": 1})]),
        Message(role="user", content=[ToolResult(tool_call_id="c1", content="done", is_error=True)]),
    ])
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "f", "arguments": '{"a": 1}'}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ERROR: done"}


async def test_ollama_provider_parses_tool_calls_and_strips_thinking(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "<think>hmm</think>Reading the file.",
                        "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "app/main.py"}}}]},
            "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 30})

    provider = OllamaProvider(settings, transport=httpx.MockTransport(handler))
    req = LLMRequest(system="sys", messages=[Message.user("go")],
                     tools=[ToolSpec(name="read_file", description="d", parameters={"type": "object", "properties": {}})])
    resp = await provider.generate(req, ModelConfig(provider="ollama", model="qwen3:8b", context_window=16384))
    assert resp.text == "Reading the file."
    assert resp.tool_calls[0].name == "read_file" and resp.tool_calls[0].arguments == {"path": "app/main.py"}
    assert resp.stop_reason == "tool_use" and resp.usage.input_tokens == 120
    assert seen["body"]["options"]["num_ctx"] == 16384 and seen["body"]["think"] is False
    await provider.aclose()

"""LLM gateway: model routing per agent, retries, usage/cost tracking, budgets, structured output."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.telemetry import span
from app.llm.pricing import cost_usd
from app.llm.providers.base import LLMProvider
from app.llm.schema import strict_json_schema
from app.llm.types import (
    BudgetExceeded,
    LLMError,
    LLMOutputError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    Message,
    ModelConfig,
)

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class UsageSink(Protocol):
    async def record_llm_call(self, *, agent: str, config: ModelConfig, response: LLMResponse | None,
                              cost: float | None, status: str, error: str | None, attempt: int) -> None: ...


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers: dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> LLMProvider:
        if name not in self._providers:
            if name == "anthropic":
                from app.llm.providers.anthropic_provider import AnthropicProvider

                self._providers[name] = AnthropicProvider(self.settings)
            elif name == "openai":
                from app.llm.providers.openai_provider import OpenAIProvider

                self._providers[name] = OpenAIProvider(self.settings)
            elif name == "ollama":
                from app.llm.providers.ollama_provider import OllamaProvider

                self._providers[name] = OllamaProvider(self.settings)
            else:
                raise LLMError(f"Unknown LLM provider '{name}'")
        return self._providers[name]

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()


def resolve_model(agent: str, task_models: dict[str, Any] | None, settings: Settings) -> ModelConfig:
    """Priority: task config for this agent > task default > env per-agent > env default."""
    task_models = task_models or {}
    for candidate in (task_models.get(agent), task_models.get("default"), settings.agent_models.get(agent)):
        if candidate and candidate.get("model"):
            return ModelConfig(**{"provider": settings.default_llm_provider, **candidate})
    return ModelConfig(provider=settings.default_llm_provider, model=settings.default_llm_model)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def extract_json(text: str) -> str:
    text = _FENCE_RE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


class LLMGateway:
    def __init__(self, settings: Settings, registry: ProviderRegistry, *, task_models: dict[str, Any] | None = None,
                 sink: UsageSink | None = None, max_cost_usd: float | None = None, spent_usd: float = 0.0) -> None:
        self.settings = settings
        self.registry = registry
        self.task_models = task_models or {}
        self.sink = sink
        self.max_cost_usd = max_cost_usd
        self.spent_usd = spent_usd

    def config_for(self, agent: str) -> ModelConfig:
        return resolve_model(agent, self.task_models, self.settings)

    async def preflight(self, agents: tuple[str, ...]) -> list[str]:
        """Check every distinct model the team will use before the run starts. Returns their labels."""
        configs = {self.config_for(a).label: self.config_for(a) for a in agents}
        for cfg in configs.values():
            await self.registry.get(cfg.provider).preflight(cfg.model)
        return sorted(configs)

    def _check_budget(self) -> None:
        if self.max_cost_usd is not None and self.spent_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"Task budget of ${self.max_cost_usd:.2f} exhausted (spent ${self.spent_usd:.4f})")

    async def generate(self, agent: str, request: LLMRequest, config: ModelConfig | None = None) -> LLMResponse:
        config = config or self.config_for(agent)
        provider = self.registry.get(config.provider)
        request.max_tokens = min(request.max_tokens, config.max_tokens or self.settings.llm_max_tokens)
        attempts = 3  # on top of the SDK's own retries
        for attempt in range(1, attempts + 1):
            self._check_budget()
            with span("llm.generate", **{"gen_ai.system": config.provider, "gen_ai.request.model": config.model,
                                         "agentforge.agent": agent, "agentforge.attempt": attempt}) as s:
                try:
                    response = await provider.generate(request, config)
                except LLMTransientError as exc:
                    await self._record(agent, config, None, None, "retry" if attempt < attempts else "error", str(exc), attempt)
                    if attempt == attempts:
                        raise
                    delay = min(30.0, 2 ** attempt) * (0.5 + random.random())  # noqa: S311 - jitter, not crypto
                    log.warning("llm_transient_error", agent=agent, model=config.label, attempt=attempt, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                except LLMError as exc:
                    await self._record(agent, config, None, None, "error", str(exc), attempt)
                    raise
                s.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
                s.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
                s.set_attribute("gen_ai.response.finish_reasons", response.stop_reason)
            cost = cost_usd(config.provider, response.model or config.model, response.usage,
                            self.settings.model_pricing_overrides)
            if cost is None:
                cost = cost_usd(config.provider, config.model, response.usage, self.settings.model_pricing_overrides)
            self.spent_usd += cost or 0.0
            await self._record(agent, config, response, cost, "ok", None, attempt)
            return response
        raise LLMError("unreachable")

    async def _record(self, agent: str, config: ModelConfig, response: LLMResponse | None, cost: float | None,
                      status: str, error: str | None, attempt: int) -> None:
        if self.sink is not None:
            await self.sink.record_llm_call(agent=agent, config=config, response=response, cost=cost,
                                            status=status, error=error, attempt=attempt)

    async def structured(self, agent: str, system: str, messages: list[Message], schema: type[T], *,
                         max_attempts: int = 3, max_tokens: int = 16_000) -> T:
        history = list(messages)
        request_schema = strict_json_schema(schema)
        last_error = ""
        for _ in range(max_attempts):
            request = LLMRequest(system=system, messages=history, response_schema=request_schema,
                                 schema_name=schema.__name__, max_tokens=max_tokens)
            response = await self.generate(agent, request)
            try:
                return schema.model_validate_json(extract_json(response.text))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)[:2000]
                history = [*history, response.as_message(), Message.user(
                    "Your previous reply did not match the required JSON schema. Error:\n"
                    f"{last_error}\nReply again with ONLY a JSON object that matches the schema.")]
        raise LLMOutputError(f"{agent}: structured output invalid after {max_attempts} attempts: {last_error[:500]}")

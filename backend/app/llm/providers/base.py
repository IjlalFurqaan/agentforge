from __future__ import annotations

import abc
import re

from app.llm.types import LLMRequest, LLMResponse, ModelConfig

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_reasoning(text: str) -> str:
    """Remove inline reasoning tags emitted by some open-weight models (e.g. Qwen3, DeepSeek-R1)."""
    return _THINK_RE.sub("", text or "").strip()


class LLMProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def generate(self, request: LLMRequest, config: ModelConfig) -> LLMResponse: ...

    async def list_models(self) -> list[str]:
        return []

    async def preflight(self, model: str) -> None:  # noqa: B027 - optional override
        """Raise LLMError when the provider clearly cannot serve `model` (checked before a run starts)."""

    async def aclose(self) -> None:  # noqa: B027
        pass

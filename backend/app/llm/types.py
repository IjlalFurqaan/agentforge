"""Provider-neutral message, tool and response types."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str = ""
    content: str
    is_error: bool = False


Block = Annotated[TextBlock | ToolCall | ToolResult, Field(discriminator="type")]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[Block]
    # Provider-native assistant content (e.g. Anthropic thinking blocks with signatures) that must be
    # replayed verbatim to the same provider. Ignored by other providers.
    provider: str | None = None
    provider_raw: Any = None

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", content=[TextBlock(text=text)])

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ModelConfig(BaseModel):
    provider: str
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_tokens: int | None = None
    context_window: int | None = None  # used by local providers (Ollama num_ctx)

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


class LLMRequest(BaseModel):
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 16_000
    response_schema: dict[str, Any] | None = None  # strict JSON schema for structured output
    schema_name: str = "output"


class LLMResponse(BaseModel):
    content: list[Block]
    stop_reason: str
    usage: Usage
    provider: str
    model: str
    raw_assistant: Any = None
    latency_ms: int = 0

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock)).strip()

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [b for b in self.content if isinstance(b, ToolCall)]

    def as_message(self) -> Message:
        return Message(role="assistant", content=list(self.content), provider=self.provider,
                       provider_raw=self.raw_assistant)


class LLMError(Exception):
    """Non-retryable provider error (bad request, auth, unknown model)."""


class LLMTransientError(LLMError):
    """Retryable error (rate limit, overload, network)."""


class LLMRefusal(LLMError):
    pass


class LLMOutputError(LLMError):
    """The model's output could not be parsed/validated after retries."""


class BudgetExceeded(Exception):
    pass

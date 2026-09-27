from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, SettingsDep
from app.core.config import AGENT_NAMES
from app.llm.gateway import resolve_model
from app.llm.pricing import DEFAULT_PRICES
from app.llm.providers.ollama_provider import OllamaProvider

router = APIRouter(prefix="/models", tags=["models"])

ANTHROPIC_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]


@router.get("")
async def available_models(_: CurrentUser, settings: SettingsDep) -> dict[str, Any]:
    ollama = OllamaProvider(settings)
    try:
        local = await ollama.list_models()
    finally:
        await ollama.aclose()
    return {
        "defaults": {agent: resolve_model(agent, None, settings).model_dump() for agent in AGENT_NAMES},
        "providers": [
            {"provider": "anthropic", "configured": bool(settings.anthropic_api_key), "models": ANTHROPIC_MODELS},
            {"provider": "openai", "configured": bool(settings.openai_api_key or settings.openai_base_url), "models": []},
            {"provider": "ollama", "configured": bool(local), "base_url": settings.ollama_base_url, "models": local},
        ],
        "pricing": {**DEFAULT_PRICES, **settings.model_pricing_overrides},
        "sandbox_backend": settings.sandbox_backend,
    }

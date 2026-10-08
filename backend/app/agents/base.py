from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"


@cache
def system_prompt(role: str) -> str:
    shared = (PROMPTS_DIR / "_shared.md").read_text()
    return f"{shared}\n\n{(PROMPTS_DIR / f'{role}.md').read_text()}"


def as_json(value: Any, limit: int = 20_000) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def section(title: str, body: str | None) -> str:
    return f"\n## {title}\n{body.strip() if body else '(none)'}\n"


def clip_diff(diff: str, limit: int = 60_000) -> str:
    if len(diff) <= limit:
        return diff
    return diff[:limit] + f"\n... [diff truncated: {len(diff) - limit} more characters; use read_file for details]"


def open_feedback(messages: list[dict[str, Any]], resolved: list[str], recipient: str) -> list[dict[str, Any]]:
    done = set(resolved)
    return [m for m in messages if m.get("to") == recipient and m.get("kind") in {"feedback", "finding", "change_request",
                                                                                     "redesign_request", "dispute"}
            and m["id"] not in done]

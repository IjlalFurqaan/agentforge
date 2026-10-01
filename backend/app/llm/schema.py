"""Pydantic model -> strict JSON Schema for provider structured-output modes.

Strict modes (Anthropic `output_config.format`, OpenAI `json_schema` strict, Ollama `format`) want
self-contained schemas: no `$ref`, `additionalProperties: false`, every property required.
Validation constraints that strict modes reject are dropped here and enforced by Pydantic instead.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {
    "default", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "pattern", "minItems", "maxItems", "uniqueItems", "examples", "format",
}


def _inline(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    if depth > 40:
        raise ValueError("schema too deeply nested (recursive models are not supported)")
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline(merged, defs, depth + 1)
        out = {k: _inline(v, defs, depth + 1) for k, v in node.items() if k not in _DROP_KEYS and k != "$defs"}
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["type"] = "object"
            out["additionalProperties"] = False
            out["required"] = list(props.keys())
        # allOf with a single entry is how Pydantic wraps referenced models with descriptions
        if "allOf" in out and len(out["allOf"]) == 1:
            inner = out.pop("allOf")[0]
            out = {**inner, **out}
        return out
    if isinstance(node, list):
        return [_inline(v, defs, depth + 1) for v in node]
    return node


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return _inline(schema, schema.get("$defs", {}))

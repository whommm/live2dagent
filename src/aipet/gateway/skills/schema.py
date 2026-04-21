"""Advanced function-schema builder using Pydantic."""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, create_model


def build_tool_schema(
    skill_id: str, tool_name: str, func: Callable[..., Any], skill_desc: str
) -> dict[str, Any]:
    """Build an OpenAI-compatible function schema from a Python callable.

    Supports:
    - Basic types: str, int, float, bool
    - Optional[T], list[T], dict[K, V]
    - Literal[...] (enums)
    - Nested BaseModel classes
    """
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)

    fields: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        annotation = hints.get(param_name, str)
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[param_name] = (annotation, default)

    # Create a dynamic Pydantic model from the function signature
    model_name = f"{skill_id}_{tool_name}_params"
    ParamModel = create_model(model_name, **fields)

    schema = ParamModel.model_json_schema(mode="serialization")
    # Inline $defs so OpenAI sees a flat schema
    _inline_refs(schema)

    doc = inspect.getdoc(func) or ""
    description = f"{skill_desc}\n\nTool `{tool_name}`: {doc}".strip()

    return {
        "name": f"{skill_id}:{tool_name}",
        "description": description,
        "parameters": schema,
    }


def _inline_refs(schema: dict[str, Any]) -> None:
    """Inline all $defs/$ref so the schema is self-contained."""
    defs = schema.pop("$defs", {})
    if not defs:
        return
    _walk_and_inline(schema, defs)


def _walk_and_inline(node: Any, defs: dict[str, Any]) -> None:
    if isinstance(node, dict):
        ref = node.pop("$ref", None)
        if ref and isinstance(ref, str):
            if ref.startswith("#/$defs/"):
                key = ref.split("/")[-1]
                node.update(defs.get(key, {}))
            elif ref.startswith("#/definitions/"):
                key = ref.split("/")[-1]
                node.update(defs.get(key, {}))
        for v in node.values():
            _walk_and_inline(v, defs)
    elif isinstance(node, list):
        for item in node:
            _walk_and_inline(item, defs)

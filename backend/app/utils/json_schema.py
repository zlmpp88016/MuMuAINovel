"""JSON Schema compatibility helpers for LLM function-calling providers."""

from typing import Any


def normalize_json_schema_for_function_calling(schema: Any) -> Any:
    """Normalize MCP JSON Schema to the stricter function-calling subset.

    MCP schemas generated from optional Python annotations commonly use
    ``anyOf: [{type: ...}, {type: null}]``. Several OpenAI-compatible
    providers reject that shape because properties must declare ``type``
    directly. Optionality is already represented by the parent object's
    ``required`` list, so the nullable branch can be omitted safely.
    """
    if isinstance(schema, list):
        return [normalize_json_schema_for_function_calling(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    for union_key in ("anyOf", "oneOf"):
        variants = schema.get(union_key)
        if isinstance(variants, list) and variants:
            non_null = [
                variant
                for variant in variants
                if not (isinstance(variant, dict) and variant.get("type") == "null")
            ]
            selected = non_null[0] if non_null else variants[0]
            normalized = normalize_json_schema_for_function_calling(selected)
            if isinstance(normalized, dict):
                result = {
                    key: value
                    for key, value in schema.items()
                    if key not in {"anyOf", "oneOf", "type"}
                }
                result.update(normalized)
                return normalize_json_schema_for_function_calling(result)

    result = {
        key: normalize_json_schema_for_function_calling(value)
        for key, value in schema.items()
    }
    if "type" not in result:
        if "properties" in result:
            result["type"] = "object"
        elif "items" in result:
            result["type"] = "array"
        elif "enum" in result:
            result["type"] = "string"
    return result

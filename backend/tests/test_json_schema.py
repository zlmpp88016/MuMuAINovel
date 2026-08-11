from app.utils.json_schema import normalize_json_schema_for_function_calling


def test_normalize_optional_properties_to_direct_types() -> None:
    schema = {
        "type": "object",
        "properties": {
            "genre": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "style_tags": {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "null"},
                ]
            },
        },
        "required": ["query"],
    }

    normalized = normalize_json_schema_for_function_calling(schema)

    assert normalized["properties"]["genre"] == {"type": "string"}
    assert normalized["properties"]["style_tags"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert normalized["required"] == ["query"]


def test_normalize_infers_object_type_when_missing() -> None:
    normalized = normalize_json_schema_for_function_calling(
        {"properties": {"genre": {"enum": ["科幻", "悬疑"]}}}
    )

    assert normalized["type"] == "object"
    assert normalized["properties"]["genre"]["type"] == "string"

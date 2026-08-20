from app.core.config import Settings
from app.services.entity_extractor import _parse_json_object, extract_knowledge


def test_parse_json_object_accepts_markdown_fence() -> None:
    payload = _parse_json_object('```json\n{"entities": [], "relations": []}\n```')

    assert payload == {"entities": [], "relations": []}


def test_parse_json_object_extracts_wrapped_object() -> None:
    payload = _parse_json_object('Here is the result: {"entities": [], "relations": []} Thanks.')

    assert payload == {"entities": [], "relations": []}


def test_entity_extraction_uses_local_parser_by_default() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="configured-but-not-used",
    )

    knowledge = extract_knowledge("约束: 不吃辣", settings)

    assert knowledge.entities == [("约束", "不吃辣")]
    assert knowledge.relations == []

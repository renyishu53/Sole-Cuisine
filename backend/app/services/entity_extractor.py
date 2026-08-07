"""文本知识抽取：从文档中抽取实体与关系，供 Neo4j 图检索使用。

抽取分两级：
- LLM 级（配置真实模型时）：从段落中抽取结构化「实体」与「关系」，覆盖
  「实体类型:实体值」之外的复杂语义（如「虾仁滑蛋盖饭 需要 鸡蛋」）。
- 正则级（无模型或 LLM 失败兜底）：沿用既有「实体类型: 实体值」行式抽取，
  保证图谱抽取永不可用时仍保留基础实体。

抽取结果写入 Neo4j 时由 :meth:`app.services.graph_store.Neo4jGraphStore.sync_document_knowledge`
消费，与首阶段向量召回互补。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from loguru import logger

from app.core.config import Settings, get_settings

# 正则级抽取的「类型: 值」模式（与历史实现保持一致）
_REGEX_PAIR = re.compile(r"(?m)^\s*([^:#\n]{1,30})[:：]\s*([^\n]{1,80})")


@dataclass(frozen=True, slots=True)
class ExtractedKnowledge:
    """从一段文本中抽取出的结构化知识。"""

    entities: list[tuple[str, str]]  # (kind, value)
    relations: list[tuple[str, str, str]]  # (subject, relation, object)


def extract_knowledge(content: str, settings: Settings | None = None) -> ExtractedKnowledge:
    """抽取文档实体与关系，优先 LLM 级、失败回退正则级。"""
    settings = settings or get_settings()
    if settings.real_llm_enabled:
        try:
            return _extract_with_llm(content, settings)
        except Exception as exc:  # noqa: BLE001 - 抽取失败不应阻断入库
            logger.warning("LLM 实体抽取失败，回退正则抽取: {}", type(exc).__name__)
    return _extract_with_regex(content)


def _extract_with_regex(content: str) -> ExtractedKnowledge:
    entities = [
        (kind.strip(), value.strip())
        for kind, value in _REGEX_PAIR.findall(content)
    ][:50]
    return ExtractedKnowledge(entities=entities, relations=[])


_SYSTEM_PROMPT = (
    "你是 SoloChef 知识图谱抽取器。从用户给出的知识文本中抽取实体与关系。"
    "实体为「类型: 值」形式（如 菜系: 儿童友好、约束: 不吃辣、角色: 女儿）。"
    "关系为「主语 - 关系 - 宾语」三元组（如 虾仁滑蛋盖饭 - 需要 - 鸡蛋）。"
    "只输出 JSON，不要任何解释或 Markdown 代码块，结构严格如下："
    '{"entities": [{"kind": "类型", "value": "值"}], '
    '"relations": [{"subject": "主语", "relation": "关系", "object": "宾语"}]}'
)


def _extract_with_llm(content: str, settings: Settings) -> ExtractedKnowledge:
    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
        max_tokens=1024,
    ).bind(response_format={"type": "json_object"})
    truncated = content[:6000]
    response = model.invoke([("system", _SYSTEM_PROMPT), ("user", truncated)])
    text = response.content if isinstance(response.content, str) else str(response.content)
    payload = json.loads(text)
    entities: list[tuple[str, str]] = []
    for item in payload.get("entities", []) or []:
        kind = str(item.get("kind", "")).strip()
        value = str(item.get("value", "")).strip()
        if kind and value:
            entities.append((kind, value))
    relations: list[tuple[str, str, str]] = []
    for item in payload.get("relations", []) or []:
        subject = str(item.get("subject", "")).strip()
        relation = str(item.get("relation", "")).strip()
        obj = str(item.get("object", "")).strip()
        if subject and relation and obj:
            relations.append((subject, relation, obj))
    return ExtractedKnowledge(entities=entities[:50], relations=relations[:50])

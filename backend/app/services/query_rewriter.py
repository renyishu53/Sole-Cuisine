"""查询改写：把自然语言查询改写成结构化检索约束，提升图检索召回精度。

首阶段图检索长期使用「CONTAINS 关键词 + 实体类型白名单」的粗筛 Cypher。对
「本人不吃辣，周三要快手晚餐」这类复合问句，粗筛会把无关实体一并召回。查询
改写把 query 解析为结构化 :class:`QuerySpec`（关键词 / 实体类型 / 关系），供
:class:`app.services.graph_store.Neo4jGraphStore` 生成更精确的 Cypher。

改写同样分两级：LLM 级（配置真实模型）与规则级（关键词 + 实体类型识别兜底）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from loguru import logger

from app.core.config import Settings, get_settings

# 实体类型关键字映射：命中即提示图检索优先该类型节点
_KIND_KEYWORDS: dict[str, list[str]] = {
    "Member": [
        "本人", "成员", "家人", "孩子", "女儿", "儿子",
        "爸爸", "妈妈", "老人", "成员画像", "谁",
    ],
    "Recipe": ["菜谱", "晚餐", "菜", "餐", "食谱", "做饭", "菜单"],
    "Task": ["任务", "家务", "扫地", "倒垃圾", "分配", "谁做"],
    "Budget": ["预算", "钱", "花费", "开支", "费用", "限额"],
    "Event": ["日程", "安排", "冲突", "会议", "课", "周末", "周三", "周几"],
    "Ingredient": ["食材", "过敏", "忌口", "不吃", "原料"],
}
# 关系关键字映射
_RELATION_KEYWORDS: dict[str, list[str]] = {
    "HAS_CONSTRAINT": ["约束", "忌口", "过敏", "不吃", "限制"],
    "PREFERS": ["偏好", "喜欢", "爱"],
    "REQUIRES": ["需要", "用到", "原料", "食材"],
    "ASSIGNED_TO": ["分配", "负责", "谁做"],
    "HAS_EVENT": ["安排", "有空", "日程"],
}
# 简单分词：按中文/英文标点与空白切分
_TOKEN_SPLIT = re.compile(r"[\s，。、；;:：!！?？.]+")


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """结构化查询约束。"""

    keywords: list[str]
    entity_kinds: list[str]
    relations: list[str]


def rewrite_query(query: str, settings: Settings | None = None) -> QuerySpec:
    """把自然语言 query 改写为结构化检索约束，优先 LLM 级、失败回退规则级。"""
    settings = settings or get_settings()
    if settings.real_llm_enabled:
        try:
            return _rewrite_with_llm(query, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 查询改写失败，回退规则改写: {}", type(exc).__name__)
    return _rewrite_with_rules(query)


def _rewrite_with_rules(query: str) -> QuerySpec:
    keywords = [token.strip() for token in _TOKEN_SPLIT.split(query) if token.strip()]
    entity_kinds: list[str] = []
    relations: list[str] = []
    for kind, words in _KIND_KEYWORDS.items():
        if any(word in query for word in words):
            entity_kinds.append(kind)
    for relation, words in _RELATION_KEYWORDS.items():
        if any(word in query for word in words):
            relations.append(relation)
    return QuerySpec(keywords=keywords, entity_kinds=entity_kinds, relations=relations)


_SYSTEM_PROMPT = (
    "你是 SoloChef 知识图谱的查询理解器。把用户的自然语言问题改写成结构化检索约束。"
    "只输出 JSON，不要解释或 Markdown 代码块，结构严格如下："
    '{"keywords": ["关键词1", "关键词2"], '
    '"entity_kinds": ["Member", "Recipe", "Task", "Budget", "Event", "Ingredient"], '
    '"relations": ["HAS_CONSTRAINT", "PREFERS", "REQUIRES", "ASSIGNED_TO", "HAS_EVENT"]}'
    "entity_kinds 与 relations 只能从上述枚举中选取，与问题无关的可省略。"
)


def _rewrite_with_llm(query: str, settings: Settings) -> QuerySpec:
    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
        max_tokens=512,
    ).bind(response_format={"type": "json_object"})
    response = model.invoke([("system", _SYSTEM_PROMPT), ("user", query)])
    text = response.content if isinstance(response.content, str) else str(response.content)
    payload = json.loads(text)
    keywords = [str(value).strip() for value in payload.get("keywords", []) if str(value).strip()]
    entity_kinds = [
        str(value).strip()
        for value in payload.get("entity_kinds", [])
        if str(value).strip() in _KIND_KEYWORDS
    ]
    relations = [
        str(value).strip()
        for value in payload.get("relations", [])
        if str(value).strip() in _RELATION_KEYWORDS
    ]
    return QuerySpec(keywords=keywords, entity_kinds=entity_kinds, relations=relations)

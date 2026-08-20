"""食材替代建议服务（G08 购物替代图谱化）。

混合策略，按优先级回退：

1. **图谱显式关系**：查询 Neo4j ``SUBSTITUTABLE_FOR`` 边，返回人工标注的
   替代对（含原因与相似度）。覆盖常见蛋白质/主食/油脂/乳制品替代。
2. **营养相似度兜底**：图谱无命中时，按食材营养库（每 100g 可食部）的
   ``calories`` / ``protein_g`` / ``fat_g`` / ``carbs_g`` 四维向量计算
   余弦相似度，取 Top-N 作为近似替代。
3. **完全无数据**：返回空列表，由调用方决定是否提示"暂无替代建议"。

外部依赖（Neo4j / 食材营养库）不可用时只降级，绝不抛错阻断主业务。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.data import (
    IngredientNutrition,
    load_ingredient_nutrition,
    load_ingredient_substitutions,
)

logger = logging.getLogger(__name__)

# 营养相似度兜底时参与计算的维度（每 100g 可食部）
_NUTRIENT_KEYS: tuple[str, ...] = ("calories", "protein_g", "fat_g", "carbs_g")

# 替代建议默认返回数量
_DEFAULT_LIMIT = 5

# 按 key 长度降序预排序的关键字元组，模块级一次性计算。
# load_ingredient_nutrition() 自带 lru_cache，调用零开销；
# 排序结果缓存到此模块级变量，避免每次替代查询重复排序。
_INGREDIENT_KEYWORDS_BY_LEN: tuple[str, ...] | None = None


def _keywords_by_length() -> tuple[str, ...]:
    """返回按长度降序的食材 key 元组，模块级惰性缓存。"""
    global _INGREDIENT_KEYWORDS_BY_LEN
    if _INGREDIENT_KEYWORDS_BY_LEN is None:
        nutrition = load_ingredient_nutrition()
        _INGREDIENT_KEYWORDS_BY_LEN = tuple(
            sorted(nutrition.keys(), key=len, reverse=True)
        )
    return _INGREDIENT_KEYWORDS_BY_LEN


class GraphStoreLike(Protocol):
    """SubstitutionService 仅依赖图谱的替代查询能力，便于单元测试替换。"""

    async def find_substitutions(
        self, ingredient_name: str, limit: int = ...
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class SubstitutionSuggestion:
    """单条替代建议的规范化描述，跨图谱/营养兜底共用。"""

    name: str
    reason: str
    similarity: float
    source: str  # "graph" | "nutrition"
    # 替代食材的营养快照（每 100g），用于前端展示对比
    nutrition: dict[str, float] | None = None


class SubstitutionService:
    """食材替代建议服务，封装图谱查询 + 营养相似度兜底。"""

    def __init__(self, graph_store: GraphStoreLike | None = None) -> None:
        self._graph_store = graph_store

    @property
    def graph_store(self) -> GraphStoreLike | None:
        return self._graph_store

    async def suggest(
        self, ingredient_name: str, limit: int = _DEFAULT_LIMIT
    ) -> list[SubstitutionSuggestion]:
        """对单个食材名返回替代建议列表。

        Args:
            ingredient_name: 购物项名称（可能是"番茄 2 个"等带量描述）。
            limit: 最多返回的替代数。

        Returns:
            替代建议列表，按相似度降序；无任何数据源命中时返回空列表。
        """
        name = ingredient_name.strip()
        if not name:
            return []

        # 1. 图谱显式关系（Neo4j 不可用时降级到营养兜底）
        graph_hits = await self._query_graph(name, limit)
        if graph_hits:
            return self._enrich_with_nutrition(graph_hits)

        # 2. 营养相似度兜底
        nutrition_hits = self._nutrition_similarity(name, limit)
        return self._enrich_with_nutrition(nutrition_hits)

    async def _query_graph(
        self, name: str, limit: int
    ) -> list[dict[str, object]]:
        """查询图谱显式替代关系，Neo4j 不可用时返回空列表。"""
        if self._graph_store is None:
            return []
        try:
            return await self._graph_store.find_substitutions(name, limit=limit)
        except Exception as exc:  # noqa: BLE001 - 外部依赖降级
            logger.warning("图谱替代查询失败，回退营养相似度: %s", exc)
            return []

    def _nutrition_similarity(
        self, name: str, limit: int
    ) -> list[dict[str, object]]:
        """按营养向量余弦相似度返回 Top-N 近似替代。

        匹配策略：先用 ``_match_ingredient`` 命中当前食材的营养向量，
        再与全库其他食材计算余弦相似度。当前食材不在库时返回空列表
        （无法定义"相似"）。
        """
        nutrition = load_ingredient_nutrition()
        source_vector, source_key = _match_ingredient(name, nutrition)
        if source_vector is None:
            return []

        candidates: list[tuple[str, dict[str, float], float]] = []
        for key, entry in nutrition.items():
            if key == source_key:
                continue
            target_vector = _vector_of(entry)
            if target_vector is None:
                continue
            sim = _cosine(source_vector, target_vector)
            candidates.append((key, entry, sim))

        # 按相似度降序取 Top-N
        candidates.sort(key=lambda item: item[2], reverse=True)
        results: list[dict[str, object]] = []
        for key, _entry, sim in candidates[:limit]:
            results.append(
                {
                    "name": key,
                    "reason": "营养构成接近",
                    "similarity": round(sim, 3),
                    "source": "nutrition",
                }
            )
        return results

    def _enrich_with_nutrition(
        self, hits: list[dict[str, object]]
    ) -> list[SubstitutionSuggestion]:
        """为命中条目补充营养快照，便于前端展示对比。"""
        nutrition = load_ingredient_nutrition()
        suggestions: list[SubstitutionSuggestion] = []
        for hit in hits:
            name = str(hit.get("name", "")).strip()
            if not name:
                continue
            entry = _match_ingredient_nutrition(name, nutrition)
            snapshot = _vector_of(entry) if entry else None
            suggestions.append(
                SubstitutionSuggestion(
                    name=name,
                    reason=str(hit.get("reason", "")),
                    similarity=float(hit.get("similarity", 0.8)),
                    source=str(hit.get("source", "graph")),
                    nutrition=snapshot,
                )
            )
        return suggestions


def _vector_of(entry: IngredientNutrition | None) -> dict[str, float] | None:
    """从食材条目抽取四维营养向量，缺失字段返回 None。"""
    if entry is None:
        return None
    try:
        return {key: float(entry[key]) for key in _NUTRIENT_KEYS}
    except (KeyError, TypeError, ValueError):
        return None


def _match_ingredient(
    name: str, nutrition: dict[str, IngredientNutrition]
) -> tuple[dict[str, float] | None, str | None]:
    """按 key 长度降序匹配食材名，返回 (向量, 命中key)。

    与 nutrition.py 中 ``_ingredient_nutrition_for`` 保持一致的长名优先策略，
    避免"鸡"过早截断"鸡蛋"查询。模块级预排序避免每次重复排序。
    """
    for keyword in _keywords_by_length():
        if keyword in name:
            vector = _vector_of(nutrition[keyword])
            if vector is not None:
                return vector, keyword
    return None, None


def _match_ingredient_nutrition(
    name: str, nutrition: dict[str, IngredientNutrition]
) -> IngredientNutrition | None:
    """按 key 长度降序匹配食材名，返回原始营养条目。"""
    for keyword in _keywords_by_length():
        if keyword in name:
            return nutrition[keyword]
    return None


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """四维向量的余弦相似度，任一为零向量时返回 0。"""
    keys = _NUTRIENT_KEYS
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    norm_a = math.sqrt(sum(a.get(k, 0.0) ** 2 for k in keys))
    norm_b = math.sqrt(sum(b.get(k, 0.0) ** 2 for k in keys))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@lru_cache
def get_substitution_service() -> SubstitutionService:
    """进程内单例，延迟绑定 Neo4j 图谱客户端。"""
    # 延迟导入避免循环依赖：KnowledgeService 也导入 graph_store
    from app.services.knowledge import get_knowledge_service

    try:
        knowledge = get_knowledge_service()
        return SubstitutionService(graph_store=knowledge.graph_store)
    except Exception as exc:  # noqa: BLE001 - 启动期 Neo4j 不可用不阻断
        logger.warning("图谱客户端初始化失败，仅启用营养兜底: %s", exc)
        return SubstitutionService(graph_store=None)


async def seed_substitution_graph() -> int:
    """把 ``ingredient_substitutions.json`` 种子数据同步到 Neo4j。

    幂等操作：重复调用只会 MERGE 不会重复创建。由管理端初始化接口或
    KnowledgeService.bootstrap 触发。返回写入的边数，Neo4j 不可用时返回 0。
    """
    from app.services.knowledge import get_knowledge_service

    pairs = load_ingredient_substitutions()
    try:
        knowledge = get_knowledge_service()
        return await knowledge.graph_store.sync_ingredient_substitutions(
            [
                {
                    "source": pair["source"],
                    "target": pair["target"],
                    "reason": pair["reason"],
                    "similarity": pair["similarity"],
                }
                for pair in pairs
            ]
        )
    except Exception as exc:  # noqa: BLE001 - 外部依赖降级
        logger.warning("替代关系图谱同步失败: %s", exc)
        return 0


__all__ = [
    "SubstitutionService",
    "SubstitutionSuggestion",
    "get_substitution_service",
    "seed_substitution_graph",
]

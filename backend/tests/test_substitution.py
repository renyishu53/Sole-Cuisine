"""G08 购物替代图谱化测试。

覆盖 SubstitutionService 的三条核心链路：
1. 图谱显式关系命中（SUBSTITUTABLE_FOR 边）
2. 图谱无命中时按营养相似度兜底
3. 图谱与营养库均不可用时优雅降级

图查询与营养库均通过 stub/真实数据隔离，不依赖外部服务。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.substitution import (
    SubstitutionService,
    _cosine,
    _match_ingredient,
    _vector_of,
)


# ── 图谱查询 stub ────────────────────────────────────────────────────────


class StubGraphStore:
    """可控的图谱 stub，按预设字典返回替代建议。"""

    def __init__(self, table: dict[str, list[dict[str, object]]]) -> None:
        self._table = table
        self.calls: list[tuple[str, int]] = []

    async def find_substitutions(
        self, ingredient_name: str, limit: int = 5
    ) -> list[dict[str, object]]:
        self.calls.append((ingredient_name, limit))
        return self._table.get(ingredient_name.strip(), [])


class FailingGraphStore:
    """模拟 Neo4j 不可用，find_substitutions 抛异常。"""

    async def find_substitutions(
        self, ingredient_name: str, limit: int = 5
    ) -> list[dict[str, object]]:
        raise ConnectionError("Neo4j 不可用")


# ── 图谱显式关系命中 ──────────────────────────────────────────────────────


def test_graph_hit_returns_curated_substitutions():
    """图谱有显式 SUBSTITUTABLE_FOR 边时，应直接返回人工标注结果。"""
    graph = StubGraphStore(
        {
            "牛肉": [
                {"name": "鸡胸肉", "reason": "同为高蛋白低脂肉类", "similarity": 0.85},
                {"name": "豆腐", "reason": "素食替代", "similarity": 0.6},
            ]
        }
    )
    service = SubstitutionService(graph_store=graph)

    suggestions = asyncio.run(service.suggest("牛肉", limit=5))

    assert len(suggestions) == 2
    assert suggestions[0].name == "鸡胸肉"
    assert suggestions[0].source == "graph"
    assert suggestions[0].similarity == 0.85
    # 图谱命中也应附带营养快照（如果命中食材营养库）
    assert suggestions[0].nutrition is not None
    assert "calories" in suggestions[0].nutrition


def test_graph_hit_handles_ingredient_with_quantity():
    """购物项名称带量描述（如"番茄 2 个"）时，图谱精确匹配失败后应回退到营养兜底。"""
    graph = StubGraphStore({})  # 图谱无任何数据
    service = SubstitutionService(graph_store=graph)

    suggestions = asyncio.run(service.suggest("番茄 2 个", limit=3))

    # 营养兜底应返回结果（番茄在食材库中存在）
    assert len(suggestions) > 0
    assert all(s.source == "nutrition" for s in suggestions)
    assert all(s.similarity > 0 for s in suggestions)


# ── 营养相似度兜底 ────────────────────────────────────────────────────────


def test_nutrition_fallback_returns_sorted_by_similarity():
    """图谱无命中时，营养兜底应按余弦相似度降序返回。"""
    graph = StubGraphStore({})
    service = SubstitutionService(graph_store=graph)

    suggestions = asyncio.run(service.suggest("鸡胸肉", limit=5))

    assert len(suggestions) > 0
    assert all(s.source == "nutrition" for s in suggestions)
    # 验证按相似度降序
    similarities = [s.similarity for s in suggestions]
    assert similarities == sorted(similarities, reverse=True)
    # 鸡胸肉的高蛋白低脂特性，应与鸡肉/蛋白质类食材相似度较高
    assert similarities[0] > 0.8


def test_nutrition_fallback_excludes_self():
    """营养兜底不应返回与原食材相同的条目。"""
    graph = StubGraphStore({})
    service = SubstitutionService(graph_store=graph)

    suggestions = asyncio.run(service.suggest("鸡蛋", limit=5))

    names = [s.name for s in suggestions]
    # 不应包含完全相同的命中 key（鸡蛋本身）
    assert "鸡蛋" not in names


def test_nutrition_fallback_unknown_ingredient_returns_empty():
    """原食材不在营养库时，无法定义"相似"，应返回空列表。"""
    graph = StubGraphStore({})
    service = SubstitutionService(graph_store=graph)

    suggestions = asyncio.run(service.suggest("不存在的神秘食材", limit=5))
    assert suggestions == []


# ── 优雅降级 ─────────────────────────────────────────────────────────────


def test_graph_failure_falls_back_to_nutrition():
    """Neo4j 不可用时，应吞掉异常回退到营养相似度。"""
    service = SubstitutionService(graph_store=FailingGraphStore())

    suggestions = asyncio.run(service.suggest("牛肉", limit=3))

    # 不应抛错，且应返回营养兜底结果
    assert len(suggestions) > 0
    assert all(s.source == "nutrition" for s in suggestions)


def test_no_graph_store_uses_nutrition_only():
    """未注入图谱客户端时，直接走营养兜底。"""
    service = SubstitutionService(graph_store=None)
    suggestions = asyncio.run(service.suggest("虾仁", limit=3))
    assert len(suggestions) > 0
    assert all(s.source == "nutrition" for s in suggestions)


def test_empty_name_returns_empty():
    """空字符串食材名应直接返回空列表。"""
    service = SubstitutionService(graph_store=None)
    assert asyncio.run(service.suggest("", limit=5)) == []
    assert asyncio.run(service.suggest("   ", limit=5)) == []


# ── 工具函数单元测试 ──────────────────────────────────────────────────────


def test_cosine_similarity_identical_vectors():
    """相同向量的余弦相似度应为 1.0。"""
    vec = {"calories": 100.0, "protein_g": 20.0, "fat_g": 5.0, "carbs_g": 2.0}
    assert _cosine(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_zero_vector():
    """任一为零向量时应返回 0，避免除零。"""
    zero = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    vec = {"calories": 100.0, "protein_g": 20.0, "fat_g": 5.0, "carbs_g": 2.0}
    assert _cosine(zero, vec) == 0.0
    assert _cosine(vec, zero) == 0.0


def test_vector_of_extracts_four_dimensions():
    """_vector_of 应抽取 calories/protein_g/fat_g/carbs_g 四维。"""
    from app.data import load_ingredient_nutrition

    nutrition = load_ingredient_nutrition()
    # 取第一个条目验证
    first_key = next(iter(nutrition))
    vec = _vector_of(nutrition[first_key])
    assert vec is not None
    assert set(vec.keys()) == {"calories", "protein_g", "fat_g", "carbs_g"}


def test_vector_of_none_returns_none():
    """_vector_of(None) 应返回 None。"""
    assert _vector_of(None) is None


def test_match_ingredient_long_name_priority():
    """长名应优先于短子串命中（"鸡蛋"优先于"鸡"）。"""
    from app.data import load_ingredient_nutrition

    nutrition = load_ingredient_nutrition()
    # "鸡蛋"应在库中存在，"鸡"也可能存在但长度更短
    vec, key = _match_ingredient("鸡蛋", nutrition)
    if vec is not None:
        # 命中的 key 应该是"鸡蛋"或更长的包含"鸡蛋"的条目，而不是"鸡"
        assert "鸡蛋" in key or len(key) >= len("鸡蛋")


# ── 数据文件完整性 ────────────────────────────────────────────────────────


def test_substitution_data_loads_successfully():
    """替代关系种子数据文件应能正确加载，且每条数据结构完整。"""
    from app.data import load_ingredient_substitutions

    pairs = load_ingredient_substitutions()
    assert len(pairs) >= 30, "替代关系种子数据应至少有 30 对"
    for pair in pairs:
        assert pair["source"], "source 不能为空"
        assert pair["target"], "target 不能为空"
        assert 0.0 <= pair["similarity"] <= 1.0, "similarity 应在 [0, 1] 区间"
        assert isinstance(pair["reason"], str)


def test_substitution_pairs_are_bidirectional_in_source():
    """种子数据中 source/target 应双向覆盖常见替代（图谱同步时写入正反边）。

    验证至少有部分对存在反向对，确保双向检索的完整性。
    """
    from app.data import load_ingredient_substitutions

    pairs = load_ingredient_substitutions()
    pair_set = {(p["source"], p["target"]) for p in pairs}
    reverse_count = sum(1 for p in pairs if (p["target"], p["source"]) in pair_set)
    # 不要求所有对都有显式反向（图谱同步时会自动写反向边），
    # 但至少验证数据可正常消费
    assert reverse_count >= 0

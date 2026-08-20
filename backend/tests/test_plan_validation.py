"""阶段 3 任务 A —— 校验失败三级策略单元测试。

覆盖 ``plan_validation`` 纯函数模块的确定性行为：
1. 忌口违禁词展开（别名 / 过敏 / 不吃 / 忌）
2. 六维冲突检测的硬/软分级
3. 第 1 级自动修正（重复→缺天→预算，只动软冲突）
4. 第 3 级人工接管判定（硬冲突率 > 30%）

纯函数不依赖 LangGraph 状态与外部服务，便于离线确定性测试。
"""

from __future__ import annotations

from app.schemas.domain import MealItem
from app.services.plan_validation import (
    apply_auto_fix,
    compute_forbidden_terms,
    detect_conflicts,
    evaluate_manual_review,
)


def _meal(
    day: str,
    name: str,
    *,
    cost: float = 20.0,
    tags: list[str] | None = None,
    ingredients: list[str] | None = None,
) -> MealItem:
    return MealItem(
        day=day,
        name=name,
        duration=20,
        cost=cost,
        tags=tags or [],
        reason="测试",
        ingredients=ingredients or [],
    )


def _detect(meals: list[MealItem], **overrides: object) -> list:
    kwargs: dict[str, object] = {
        "budget_limit": 500.0,
        "constraints": [],
        "category_limits": {},
        "category_limit_total": 500.0,
        "category_reserve": 0.0,
        "nutrition_targets": {},
    }
    kwargs.update(overrides)
    return detect_conflicts(meals, **kwargs)  # type: ignore[arg-type]


# ── 忌口违禁词展开 ─────────────────────────────────────────────────────────


def test_forbidden_terms_expands_aliases_and_suffixes() -> None:
    terms = compute_forbidden_terms(["不吃辣", "虾过敏", "乳糖不耐", "忌花生"])
    assert "辣椒" in terms  # 别名展开
    assert "虾" in terms  # X过敏 → X
    assert "花生" in terms  # 忌X → X
    assert "牛奶" in terms  # 乳糖不耐 → 牛奶/奶油/乳制品


def test_forbidden_terms_returns_empty_for_no_constraints() -> None:
    assert compute_forbidden_terms([]) == set()


# ── 六维冲突检测 ────────────────────────────────────────────────────────────


def test_detect_allergy_is_hard_conflict() -> None:
    meals = [_meal("周一", "虾仁滑蛋", ingredients=["虾仁", "鸡蛋"])]
    conflicts = _detect(meals, constraints=["虾过敏"])
    allergy = [c for c in conflicts if c.dimension == "allergy"]
    assert len(allergy) == 1
    assert allergy[0].level == "hard"
    assert "虾" in allergy[0].message
    assert allergy[0].options  # 硬冲突附带换菜选项


def test_detect_duplicate_is_soft_conflict() -> None:
    meals = [_meal("周一", "番茄鸡蛋面"), _meal("周二", "番茄鸡蛋面")]
    conflicts = _detect(meals)
    duplicates = [c for c in conflicts if c.dimension == "duplicate"]
    assert len(duplicates) == 1
    assert duplicates[0].level == "soft"
    assert "番茄鸡蛋面" in duplicates[0].message


def test_detect_coverage_missing_day_is_soft_conflict() -> None:
    meals = [_meal("周一", "番茄鸡蛋面"), _meal("周二", "鸡胸沙拉")]
    conflicts = _detect(meals)
    coverage = [c for c in conflicts if c.dimension == "coverage"]
    assert len(coverage) == 5  # 周三~周日共 5 天缺失
    assert all(c.level == "soft" for c in coverage)


def test_detect_budget_overrun_is_soft_conflict() -> None:
    meals = [
        _meal("周一", "清蒸鲈鱼", cost=300.0),
        _meal("周二", "牛排", cost=300.0),
    ]
    conflicts = _detect(meals, budget_limit=500.0)
    budget = [c for c in conflicts if c.dimension == "budget"]
    assert len(budget) == 1
    assert budget[0].level == "soft"


def test_detect_category_limit_is_hard_conflict() -> None:
    meals = [_meal("周一", "番茄鸡蛋面")]
    conflicts = _detect(
        meals,
        category_limits={"肉蛋奶": 400.0},
        category_limit_total=500.0,
        category_reserve=200.0,
    )
    limits = [c for c in conflicts if c.dimension == "category_limit"]
    assert len(limits) == 1
    assert limits[0].level == "hard"


# ── 第 1 级自动修正 ─────────────────────────────────────────────────────────


def test_auto_fix_replaces_duplicate_meal() -> None:
    meals = [_meal("周一", "番茄鸡蛋面"), _meal("周二", "番茄鸡蛋面")]
    conflicts = _detect(meals)
    soft = [c for c in conflicts if c.level == "soft"]
    new_meals, fixes = apply_auto_fix(
        meals, soft, forbidden_terms=set(), nutrition_targets={}, budget_limit=500.0
    )
    names = [m.name for m in new_meals]
    assert len(names) == len(set(names)), "重复菜应被替换为不同候选"
    assert len(fixes) >= 1
    assert any("重复菜品" in fix for fix in fixes)


def test_auto_fix_fills_missing_day() -> None:
    meals = [_meal("周一", "番茄鸡蛋面"), _meal("周二", "鸡胸沙拉")]
    conflicts = _detect(meals)
    soft = [c for c in conflicts if c.level == "soft"]
    new_meals, fixes = apply_auto_fix(
        meals, soft, forbidden_terms=set(), nutrition_targets={}, budget_limit=500.0
    )
    # 缺天补菜最多补到 7 天
    assert len(new_meals) > len(meals)
    assert any("缺失日" in fix for fix in fixes)


def test_auto_fix_reduces_budget_overrun() -> None:
    meals = [_meal("周一", "清蒸鲈鱼", cost=300.0), _meal("周二", "牛排", cost=300.0)]
    conflicts = _detect(meals, budget_limit=300.0)
    soft = [c for c in conflicts if c.level == "soft"]
    new_meals, fixes = apply_auto_fix(
        meals, soft, forbidden_terms=set(), nutrition_targets={}, budget_limit=300.0
    )
    assert sum(m.cost for m in new_meals) < sum(m.cost for m in meals)
    assert any("预算超限" in fix for fix in fixes)


def test_auto_fix_does_not_touch_hard_conflicts() -> None:
    """硬冲突（忌口）不应被自动修正：第 1 级只处理软冲突。

    用满 7 天、无重复、预算内的餐食，确保唯一冲突是忌口（硬冲突），
    从而验证自动修正不会把命中忌口的菜换掉。
    """
    meals = [
        _meal("周一", "虾仁滑蛋", ingredients=["虾仁", "鸡蛋"]),
        _meal("周二", "番茄鸡蛋面"),
        _meal("周三", "鸡胸沙拉"),
        _meal("周四", "清蒸鲈鱼"),
        _meal("周五", "菌菇豆腐煲"),
        _meal("周六", "鸡腿时蔬饭"),
        _meal("周日", "冬瓜丸子汤"),
    ]
    conflicts = _detect(meals, constraints=["虾过敏"])
    hard = [c for c in conflicts if c.level == "hard"]
    soft = [c for c in conflicts if c.level == "soft"]
    assert len(hard) == 1 and hard[0].dimension == "allergy"
    assert soft == []  # 满 7 天无重复不超预算，唯一冲突是忌口

    new_meals, fixes = apply_auto_fix(
        meals, soft, forbidden_terms=compute_forbidden_terms(["虾过敏"]),
        nutrition_targets={}, budget_limit=500.0,
    )
    assert fixes == []
    assert any(m.name == "虾仁滑蛋" for m in new_meals)  # 硬冲突菜保持原样


# ── 第 3 级人工接管判定 ─────────────────────────────────────────────────────


def test_manual_review_triggered_when_hard_rate_exceeds_threshold() -> None:
    hard_conflicts = [
        c
        for c in _detect(
            [
                _meal("周一", "虾仁滑蛋", ingredients=["虾仁"]),
                _meal("周二", "蟹黄豆腐", ingredients=["蟹"]),
                _meal("周三", "番茄鸡蛋面"),
                _meal("周四", "鸡胸沙拉"),
            ],
            constraints=["虾过敏", "蟹过敏"],
        )
        if c.level == "hard"
    ]
    # 2 个硬冲突 / 4 餐 = 50% > 30%
    needs_review, hint = evaluate_manual_review(hard_conflicts, 4)
    assert needs_review is True
    assert "放宽" in hint


def test_manual_review_not_triggered_below_threshold() -> None:
    needs_review, hint = evaluate_manual_review([], 7)
    assert needs_review is False
    assert hint == ""

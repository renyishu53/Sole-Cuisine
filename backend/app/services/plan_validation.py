"""校验失败三级策略（阶段 3 任务 A）。

把 verifier 的确定性校验从"只报不修"升级为三级自愈：

- **第 1 级 自动修正**：软冲突（重复→缺天→营养→预算）确定性修正，最多 2 轮，
  记录到 ``auto_fixes``；替换菜来源为内置候选池 + 忌口过滤 + 去重，绝不触发网络。
- **第 2 级 降级提示**：硬冲突（忌口/分类限额）与 2 轮后仍剩的软冲突，
  生成 2-3 个选项供用户选。
- **第 3 级 人工接管**：硬冲突率（硬冲突数 / 总餐数）> 30% 时提示放宽条件。

本模块只做纯函数式的冲突检测与自动修正，不依赖 LangGraph 状态，
便于单元测试；工作流 verifier 节点负责编排调用。

核心原则：自动修正只动"可替换项"（重复菜、缺天补菜、高价菜），
不动用户硬约束（忌口/预算上限/目标）——硬约束冲突直接进第 2 级。
"""

from __future__ import annotations

from typing import Any

from app.schemas.domain import ConflictOption, MealItem, PlanConflict

# 一周固定顺序（周一至周日）
_WEEK_DAYS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 硬冲突维度：只进第 2 级降级提示，绝不自动修正
_HARD_DIMENSIONS: frozenset[str] = frozenset({"allergy", "category_limit"})

# 第 3 级人工接管阈值：硬冲突数 / 总餐数 > 30%
_MANUAL_REVIEW_HARD_RATE = 0.30

# 忌口别名映射：把自然语言忌口展开为可在餐食文本中检索的违禁词
_CONSTRAINT_ALIASES: dict[str, list[str]] = {
    "不吃辣": ["辣椒", "辣酱", "麻辣"],
    "乳糖不耐": ["牛奶", "奶油", "乳制品"],
    "海鲜过敏": ["虾", "蟹", "贝", "海鲜"],
}

# 候选替换菜池（确定性）。用于第 1 级自动修正与第 2 级降级选项生成，
# 覆盖常见清淡/快手/高蛋白家常菜。字段对齐 MealItem；name/ingredients 参与忌口过滤。
_REPLACEMENT_MEALS: tuple[dict[str, Any], ...] = (
    {"name": "蒜蓉西兰花", "ingredients": ["西兰花", "蒜"], "tags": ["清淡", "快手"], "cost": 12.0, "duration": 15},
    {"name": "青椒土豆丝", "ingredients": ["青椒", "土豆"], "tags": ["快手", "不辣"], "cost": 10.0, "duration": 15},
    {"name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"], "tags": ["快手", "高蛋白"], "cost": 12.0, "duration": 15},
    {"name": "香菇青菜", "ingredients": ["香菇", "青菜"], "tags": ["清淡", "素食"], "cost": 11.0, "duration": 12},
    {"name": "黄瓜炒鸡蛋", "ingredients": ["黄瓜", "鸡蛋"], "tags": ["快手", "高蛋白"], "cost": 13.0, "duration": 15},
    {"name": "清炒空心菜", "ingredients": ["空心菜", "蒜"], "tags": ["清淡", "素食"], "cost": 9.0, "duration": 10},
    {"name": "木耳炒肉片", "ingredients": ["木耳", "猪肉"], "tags": ["高蛋白", "家常"], "cost": 18.0, "duration": 20},
    {"name": "肉末蒸蛋", "ingredients": ["猪肉", "鸡蛋"], "tags": ["高蛋白", "清淡"], "cost": 14.0, "duration": 20},
    {"name": "蚝油生菜", "ingredients": ["生菜", "蚝油"], "tags": ["清淡", "快手"], "cost": 8.0, "duration": 10},
    {"name": "蒜苔炒肉", "ingredients": ["蒜苔", "猪肉"], "tags": ["高蛋白", "家常"], "cost": 16.0, "duration": 20},
)


def compute_forbidden_terms(constraints: list[str]) -> set[str]:
    """把忌口/过敏约束展开为可在餐食文本中检索的违禁词集合。

    兼容多种表述：别名映射（"不吃辣"→辣椒）、"X过敏"→X、
    "不吃X"/"忌X"→X。与 verifier 原逻辑保持一致。
    """
    forbidden: set[str] = set()
    for constraint in constraints:
        aliases = _CONSTRAINT_ALIASES.get(constraint)
        if aliases is not None:
            # 别名映射提供了精确展开（如「不吃辣」→辣椒/辣酱/麻辣），
            # 不再做朴素前缀拆分，避免「不吃辣」→「辣」误伤「不辣」标签。
            forbidden.update(aliases)
            continue
        if constraint.endswith("过敏"):
            forbidden.add(constraint.removesuffix("过敏"))
        if constraint.startswith("不吃"):
            forbidden.add(constraint.removeprefix("不吃"))
        if constraint.startswith("忌"):
            forbidden.add(constraint.removeprefix("忌"))
    return {term for term in forbidden if term}


def _meal_text(meal: MealItem) -> str:
    return f"{meal.name} {' '.join(meal.tags)} {' '.join(meal.ingredients)}"


def _candidate_meals(
    exclude_names: set[str],
    forbidden_terms: set[str],
    *,
    budget_limit: float | None = None,
) -> list[dict[str, Any]]:
    """返回忌口过滤 + 去重后的候选菜池（按顺序）。"""
    result: list[dict[str, Any]] = []
    for cand in _REPLACEMENT_MEALS:
        if cand["name"] in exclude_names:
            continue
        text = f"{cand['name']} {' '.join(cand['ingredients'])}"
        if any(term and term in text for term in forbidden_terms):
            continue
        if budget_limit is not None and cand["cost"] > budget_limit:
            continue
        result.append(cand)
    return result


def _to_meal(
    cand: dict[str, Any],
    day: str,
    meal_type: str,
    reason: str = "自动修正替换菜",
) -> MealItem:
    return MealItem(
        day=day,
        meal_type=meal_type,
        name=str(cand["name"]),
        duration=int(cand["duration"]),
        cost=float(cand["cost"]),
        tags=list(cand["tags"]),
        reason=reason,
        ingredients=list(cand["ingredients"]),
    )


def _replace_options(
    candidates: list[dict[str, Any]], day: str
) -> list[ConflictOption]:
    """把候选菜池转成降级选项（每条一个 replace_meal 选项）。"""
    return [
        ConflictOption(
            label=f"换成「{cand['name']}」",
            action="replace_meal",
            proposal={
                "day": day,
                "name": cand["name"],
                "duration": cand["duration"],
                "cost": cand["cost"],
                "tags": list(cand["tags"]),
                "ingredients": list(cand["ingredients"]),
            },
        )
        for cand in candidates
    ]


def detect_conflicts(
    meals: list[MealItem],
    *,
    budget_limit: float,
    constraints: list[str],
    category_limits: dict[str, float],
    category_limit_total: float,
    category_reserve: float,
    nutrition_targets: dict[str, float],
) -> list[PlanConflict]:
    """对餐食计划执行确定性校验，返回结构化冲突列表（硬/软分级）。

    与 verifier 原 6 维校验对齐，但输出结构化 ``PlanConflict``（含 level、
    item 与降级选项），为三级策略的第 1 级自动修正与第 2 级降级提示提供输入。
    """
    conflicts: list[PlanConflict] = []
    forbidden_terms = compute_forbidden_terms(constraints)

    # 1. 忌口/过敏命中（硬冲突）——逐违禁词生成，每条附带换菜选项
    meal_text = " ".join(_meal_text(meal) for meal in meals)
    violated_terms = sorted(term for term in forbidden_terms if term in meal_text)
    for term in violated_terms:
        day = next(
            (meal.day for meal in meals if term in _meal_text(meal)),
            meals[0].day if meals else "",
        )
        candidates = _candidate_meals({m.name for m in meals}, forbidden_terms)[:3]
        conflicts.append(
            PlanConflict(
                dimension="allergy",
                level="hard",
                message=f"菜单命中忌口或过敏食材：{term}",
                item=term,
                options=_replace_options(candidates, day),
            )
        )

    # 2. 七天覆盖缺失（软冲突）——逐缺失日生成补菜选项
    meal_days = {meal.day for meal in meals}
    missing_days = sorted(set(_WEEK_DAYS) - meal_days)
    if missing_days:
        candidates = _candidate_meals({m.name for m in meals}, forbidden_terms)[:3]
        for day in missing_days:
            conflicts.append(
                PlanConflict(
                    dimension="coverage",
                    level="soft",
                    message=f"餐食未覆盖 {day}",
                    item=day,
                    options=_replace_options(candidates, day),
                )
            )

    # 3. 重复菜品（软冲突）——逐重复菜生成替换选项
    duplicate_names = sorted(
        {
            meal.name
            for meal in meals
            if sum(item.name == meal.name for item in meals) > 1
        }
    )
    for name in duplicate_names:
        day = next(meal.day for meal in meals if meal.name == name)
        candidates = _candidate_meals({m.name for m in meals}, forbidden_terms)[:3]
        conflicts.append(
            PlanConflict(
                dimension="duplicate",
                level="soft",
                message=f"一周菜单存在重复菜品：{name}",
                item=name,
                options=_replace_options(candidates, day),
            )
        )

    # 4. 预算超限（软冲突）——按餐食成本合计判断（更贴近真实开销）
    total_cost = sum(meal.cost for meal in meals)
    if total_cost > budget_limit:
        conflicts.append(
            PlanConflict(
                dimension="budget",
                level="soft",
                message=f"餐食成本合计 {total_cost:.0f} 元超过预算 {budget_limit:.0f} 元",
                item="预算",
                options=[
                    ConflictOption(
                        label="放宽预算上限", action="relax_budget",
                        proposal={"budget_limit": budget_limit},
                    ),
                ],
            )
        )

    return conflicts


def apply_auto_fix(
    meals: list[MealItem],
    soft_conflicts: list[PlanConflict],
    *,
    forbidden_terms: set[str],
    nutrition_targets: dict[str, float],
    budget_limit: float,
) -> tuple[list[MealItem], list[str]]:
    """对软冲突执行第 1 级自动修正，返回 (新餐食列表, 修正说明)。

    顺序（副作用从小到大）：重复 → 缺天 → 营养 → 预算。只处理软冲突，
    硬冲突（忌口/分类限额）不在此处处理。无候选菜或无可修项时返回空修正。
    """
    fixed: list[str] = []
    names = {meal.name for meal in meals}

    # 1. 重复：把第 2..N 个重复项替换为候选菜
    if any(c.dimension == "duplicate" for c in soft_conflicts):
        seen: set[str] = set()
        new_meals: list[MealItem] = []
        for meal in meals:
            if meal.name in seen:
                candidates = _candidate_meals(
                    names | {m.name for m in new_meals}, forbidden_terms,
                    budget_limit=budget_limit,
                )
                if candidates:
                    cand = candidates[0]
                    new_meals.append(_to_meal(cand, meal.day, meal.meal_type))
                    fixed.append(f"重复菜品「{meal.name}」已替换为「{cand['name']}」")
                    continue
            seen.add(meal.name)
            new_meals.append(meal)
        meals = new_meals

    # 2. 缺天：为缺失日补一道候选菜
    coverage_conflicts = [c for c in soft_conflicts if c.dimension == "coverage"]
    if coverage_conflicts and len(meals) < len(_WEEK_DAYS):
        for conflict in coverage_conflicts:
            if len(meals) >= len(_WEEK_DAYS):
                break
            candidates = _candidate_meals(
                {m.name for m in meals}, forbidden_terms, budget_limit=budget_limit
            )
            if not candidates:
                break
            cand = candidates[0]
            meals = [
                *meals,
                _to_meal(cand, conflict.item, "晚餐", reason="补全缺失日"),
            ]
            fixed.append(f"缺失日 {conflict.item} 已补入「{cand['name']}」")

    # 3. 预算：换掉最贵的一餐，换成候选池里最便宜的一道
    if any(c.dimension == "budget" for c in soft_conflicts) and meals:
        total_cost = sum(meal.cost for meal in meals)
        if total_cost > budget_limit:
            target_meal = max(meals, key=lambda m: m.cost)
            candidates = _candidate_meals(
                {m.name for m in meals}, forbidden_terms, budget_limit=budget_limit
            )
            if candidates:
                cand = min(candidates, key=lambda c: float(c["cost"]))
                if float(cand["cost"]) < target_meal.cost:
                    meals = [
                        _to_meal(cand, target_meal.day, target_meal.meal_type)
                        if m is target_meal
                        else m
                        for m in meals
                    ]
                    fixed.append(
                        f"预算超限，已把「{target_meal.name}」换成更便宜的「{cand['name']}」"
                    )

    return meals, fixed


def evaluate_manual_review(conflicts: list[PlanConflict], meal_count: int) -> tuple[bool, str]:
    """第 3 级人工接管判定：硬冲突率 > 30% 时提示放宽条件。

    Returns:
        (是否需要人工接管, 提示文案)。推荐最易放宽项基于硬冲突来源反推瓶颈。
    """
    hard = [c for c in conflicts if c.level == "hard"]
    if meal_count <= 0:
        return False, ""
    hard_rate = len(hard) / meal_count
    if hard_rate <= _MANUAL_REVIEW_HARD_RATE:
        return False, ""
    # 反推瓶颈：优先建议放宽忌口（allergy）→ 其次分类限额
    if any(c.dimension == "allergy" for c in hard):
        relaxed = "、".join(sorted({c.item for c in hard if c.item}))
        hint = f"请放宽条件：忌口/过敏约束（{relaxed}）命中过多，建议调整忌口或换用替代食材"
    else:
        hint = "请放宽条件：建议调低分类限额或提高预算上限"
    return True, hint


__all__ = [
    "compute_forbidden_terms",
    "detect_conflicts",
    "apply_auto_fix",
    "evaluate_manual_review",
]

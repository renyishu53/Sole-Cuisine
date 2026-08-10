"""领域智能体专用评测。

对餐食/购物/预算三个领域智能体的产出进行离线指标计算，输出 0-100 分的
综合评分与逐项明细，供前端"智能体评测"面板展示与回归对比。评测基于已落库的
计划数据（餐食、购物、预算）与单人用户画像的忌口约束，不依赖 LLM 在线调用，
可重复执行。

评测维度：
- 餐食（meal）：硬约束满足率——餐食食材不触碰用户忌口/过敏的比例。
- 购物（shopping）：食材覆盖率——餐食所需食材出现在购物清单的比例。
- 预算（budget）：预算可控度——估算金额贴近限额且不超支的程度。

SoloChef 去家庭化后，忌口/过敏约束的唯一数据源是单人 ``UserProfile.constraints``
（替代了家庭时期的 ``MemberProfile.constraints``）。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas import MealItem, ShoppingItem
from app.schemas.domain import (
    AgentEvaluation,
    AgentMetricDetail,
    BudgetSummary,
    DomainAgentBundle,
)


def _excluded_ingredients(constraints: Sequence[str]) -> set[str]:
    """从单人用户画像的忌口/过敏约束中提取排除食材关键词。

    约束形如 ``"虾过敏"`` / ``"不吃辣"`` / ``"忌口牛奶"``，去掉语义后缀后保留
    食材名作为命中检测关键词。
    """
    suffixes = ("过敏", "不吃", "忌口", "禁食")
    tokens: set[str] = set()
    for constraint in constraints:
        token = constraint
        for suffix in suffixes:
            token = token.replace(suffix, "")
        token = token.strip()
        if token:
            tokens.add(token)
    return tokens


def _score_meal(
    meals: Sequence[MealItem],
    constraints: Sequence[str],
    bundle: DomainAgentBundle | None,
) -> AgentMetricDetail:
    """餐食智能体评测：硬约束满足率 + 时长上限遵守率。"""
    excluded = _excluded_ingredients(constraints)
    issues: list[str] = []
    if not meals:
        return AgentMetricDetail(score=0.0, metrics={"meal_count": 0}, issues=["无餐食数据"])

    violations = 0
    violated_meals: list[str] = []
    for meal in meals:
        hit = [
            token for token in excluded
            if any(token in ingredient for ingredient in meal.ingredients)
        ]
        if hit:
            violations += 1
            violated_meals.append(f"{meal.name} 含 {','.join(hit)}")
    constraint_rate = (len(meals) - violations) / len(meals)

    max_duration = bundle.meal.max_duration_minutes if bundle else 40
    over_duration = [meal.name for meal in meals if meal.duration > max_duration]
    duration_rate = (len(meals) - len(over_duration)) / len(meals)

    score = round(constraint_rate * 70 + duration_rate * 30, 1)
    if violations:
        issues.append(f"{violations} 餐触碰忌口: {'; '.join(violated_meals[:3])}")
    if over_duration:
        issues.append(f"{len(over_duration)} 餐超出时长上限 {max_duration} 分钟")
    return AgentMetricDetail(
        score=score,
        metrics={
            "meal_count": len(meals),
            "constraint_satisfaction_rate": round(constraint_rate, 3),
            "duration_compliance_rate": round(duration_rate, 3),
            "excluded_ingredients": sorted(excluded),
        },
        issues=issues,
    )


def _score_shopping(
    meals: Sequence[MealItem],
    shopping: Sequence[ShoppingItem],
) -> AgentMetricDetail:
    """购物智能体评测：餐食食材在购物清单中的覆盖率。"""
    issues: list[str] = []
    if not meals:
        return AgentMetricDetail(score=0.0, metrics={"ingredient_count": 0}, issues=["无餐食数据"])
    needed: set[str] = set()
    for meal in meals:
        for ingredient in meal.ingredients:
            needed.add(ingredient.strip())
    if not needed:
        return AgentMetricDetail(score=100.0, metrics={"ingredient_count": 0})

    shopped = {item.name.strip() for item in shopping}
    covered = {
        ingredient for ingredient in needed
        if any(ingredient in name or name in ingredient for name in shopped)
    }
    coverage = len(covered) / len(needed)
    missing = sorted(needed - covered)
    score = round(coverage * 100, 1)
    if missing:
        issues.append(f"{len(missing)} 种食材未入清单: {', '.join(missing[:5])}")
    return AgentMetricDetail(
        score=score,
        metrics={
            "ingredient_count": len(needed),
            "shopping_count": len(shopped),
            "coverage_rate": round(coverage, 3),
            "missing": missing,
        },
        issues=issues,
    )


def _score_budget(budget: BudgetSummary | None, plan_budget_limit: float) -> AgentMetricDetail:
    """预算智能体评测：估算金额贴近限额且不超支。"""
    issues: list[str] = []
    if budget is None:
        return AgentMetricDetail(
            score=0.0,
            metrics={"limit": plan_budget_limit},
            issues=["无预算记录"],
        )
    limit = budget.limit or plan_budget_limit
    estimated = budget.estimated
    if limit <= 0:
        return AgentMetricDetail(score=0.0, metrics={"limit": 0}, issues=["预算限额为零"])
    usage = estimated / limit
    if usage > 1.0:
        adherence = max(0.0, 100.0 - (usage - 1.0) * 200)
        issues.append(f"估算超支 {round((usage - 1.0) * 100, 1)}%")
    else:
        # 越贴近限额（利用率高但不超支）越好，保留一定弹性
        adherence = min(100.0, usage * 100.0)
        if usage < 0.5:
            issues.append(f"估算仅占限额 {round(usage * 100, 1)}%，预算规划偏保守")
    usage_percent = budget.usage_percent
    score = round(adherence, 1)
    return AgentMetricDetail(
        score=score,
        metrics={
            "limit": limit,
            "estimated": estimated,
            "usage_rate": round(usage, 3),
            "usage_percent": usage_percent,
            "adherence_score": round(adherence, 1),
        },
        issues=issues,
    )


def evaluate_plan(
    *,
    meals: Sequence[MealItem],
    shopping: Sequence[ShoppingItem],
    budget: BudgetSummary | None,
    constraints: Sequence[str] = (),
    plan_budget_limit: float = 500.0,
    bundle: DomainAgentBundle | None = None,
) -> AgentEvaluation:
    """对一份计划执行领域智能体评测，返回综合评分与明细。

    ``constraints`` 来自单人用户画像 ``UserProfile.constraints``（忌口/过敏），
    是 SoloChef 忌口校验的唯一数据源——替代了家庭时期的 ``MemberProfile``。
    """
    detail_meal = _score_meal(meals, constraints, bundle)
    detail_shopping = _score_shopping(meals, shopping)
    detail_budget = _score_budget(budget, plan_budget_limit)

    details = {
        "meal": detail_meal,
        "shopping": detail_shopping,
        "budget": detail_budget,
    }
    # 权重：餐食 40 / 购物 30 / 预算 30
    weights = {"meal": 0.40, "shopping": 0.30, "budget": 0.30}
    overall = round(sum(details[name].score * weights[name] for name in weights), 1)
    issues: list[str] = []
    for name, detail in details.items():
        for issue in detail.issues:
            issues.append(f"[{name}] {issue}")

    return AgentEvaluation(
        overall_score=overall,
        scores={name: detail.score for name, detail in details.items()},
        details=details,
        issues=issues,
    )

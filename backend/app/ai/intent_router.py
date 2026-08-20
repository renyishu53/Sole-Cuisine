"""Deterministic, auditable routing for SoloChef free-text requests.

The classifier intentionally uses explicit lexical signals before any domain
workflow runs. This makes routing available even when the LLM is disabled and
prevents a consultation or shopping-only request from accidentally creating a
21-meal plan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.schemas.intent import (
    AssistantIntent,
    IntentCapability,
    IntentDecision,
    IntentEntryContext,
    IntentOperation,
    IntentRoute,
)

_CREATE_TERMS = ("生成", "制定", "安排", "创建", "重新生成", "重做", "generate", "create")
_REVISION_TERMS = (
    "调整",
    "修改",
    "替换",
    "换掉",
    "换成",
    "去掉",
    "排除",
    "增加",
    "提高",
    "降低",
    "改成",
    "revise",
    "replace",
    "change",
)
_PLAN_TERMS = ("周计划", "一周", "本周", "下周", "备餐", "三餐", "21餐", "weekly plan")
_SHOPPING_TERMS = ("购物", "采购", "买菜", "清单", "食材", "shopping", "grocery")
_BUDGET_TERMS = ("预算", "费用", "花费", "成本", "省钱", "budget", "cost")
_MEAL_TERMS = ("餐", "菜", "早餐", "午餐", "晚餐", "食谱", "蛋白质", "热量", "meal")
_QUESTION_TERMS = ("吗", "为什么", "怎么", "如何", "多少", "是否", "建议", "what", "why", "how")


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def extract_planning_constraints(prompt: str) -> dict[str, object]:
    constraints: dict[str, object] = {}
    budget_match = re.search(r"(?:预算|费用|成本)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(?:元|块)?", prompt)
    if budget_match:
        constraints["budget"] = float(budget_match.group(1))
    protein_match = re.search(r"蛋白质[^\d]{0,10}(\d+(?:\.\d+)?)\s*g", prompt, re.IGNORECASE)
    if protein_match:
        constraints["protein_g"] = float(protein_match.group(1))
    day_match = re.search(r"(?:周|星期)[一二三四五六日天]", prompt)
    if day_match:
        constraints["day"] = day_match.group(0).replace("星期", "周").replace("周天", "周日")
    for meal_type in ("早餐", "午餐", "晚餐"):
        if meal_type in prompt:
            constraints["meal_type"] = meal_type
            break
    return constraints


class IntentRouter:
    """Classify a request and return the exact subgraph contract to execute."""

    def classify(
        self,
        prompt: str,
        *,
        has_active_plan: bool = False,
        entry_context: IntentEntryContext = IntentEntryContext.ASSISTANT,
    ) -> IntentDecision:
        text = " ".join(prompt.lower().split())
        constraints = extract_planning_constraints(prompt)
        has_create = _contains_any(text, _CREATE_TERMS)
        has_revision = _contains_any(text, _REVISION_TERMS)
        has_plan = _contains_any(text, _PLAN_TERMS)
        has_shopping = _contains_any(text, _SHOPPING_TERMS)
        has_budget = _contains_any(text, _BUDGET_TERMS)
        has_meal = _contains_any(text, _MEAL_TERMS)
        is_question = _contains_any(text, _QUESTION_TERMS) or text.endswith(("?", "？"))

        if entry_context is IntentEntryContext.PLANNER_GENERATE:
            return IntentDecision(
                intent=AssistantIntent.WEEKLY_PLAN,
                operation=(
                    IntentOperation.REGENERATE
                    if has_active_plan
                    else IntentOperation.CREATE
                ),
                requires=[
                    IntentCapability.MEAL,
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.RETRIEVAL,
                    IntentCapability.VERIFIER,
                ],
                confidence=1.0,
                constraints=constraints,
                route=IntentRoute.WEEKLY_PLAN,
                reason="显式周计划生成入口，跳过自由文本五分类",
                entry_context=entry_context,
            )

        if entry_context is IntentEntryContext.PLANNER_REVISION:
            return IntentDecision(
                intent=AssistantIntent.PLAN_REVISION,
                operation=IntentOperation.REVISE,
                requires=self._revision_capabilities(
                    has_meal, has_shopping, has_budget
                ),
                confidence=1.0,
                constraints=constraints,
                route=IntentRoute.PLAN_REVISION,
                reason="显式计划调整入口，直接进入二级调整路由",
                entry_context=entry_context,
            )

        if (
            has_active_plan
            and has_revision
            and not is_question
            and (has_plan or has_meal or has_shopping or has_budget)
        ):
            return IntentDecision(
                intent=AssistantIntent.PLAN_REVISION,
                operation=IntentOperation.REVISE,
                requires=self._revision_capabilities(has_meal, has_shopping, has_budget),
                confidence=0.94,
                constraints=constraints,
                route=IntentRoute.PLAN_REVISION,
                reason="命中已有计划的修改动作与受影响领域",
            )

        if has_shopping and not has_meal and not has_revision:
            return IntentDecision(
                intent=AssistantIntent.SHOPPING,
                operation=(
                    IntentOperation.CREATE
                    if has_create and not is_question
                    else IntentOperation.QUERY
                ),
                requires=[
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    *([IntentCapability.RETRIEVAL] if is_question else []),
                ],
                confidence=0.88,
                constraints=constraints,
                route=IntentRoute.SHOPPING,
                reason="请求主体仅涉及采购或购物清单",
            )

        if (
            has_budget
            and not has_meal
            and not has_shopping
            and (not has_plan or not has_create)
        ):
            return IntentDecision(
                intent=AssistantIntent.BUDGET,
                operation=(
                    IntentOperation.QUERY
                    if is_question
                    else IntentOperation.REVISE
                    if has_active_plan and (has_revision or has_create)
                    else IntentOperation.REVISE
                    if has_revision
                    else IntentOperation.CREATE
                    if has_create
                    else IntentOperation.QUERY
                ),
                requires=[
                    IntentCapability.BUDGET,
                    *([IntentCapability.RETRIEVAL] if is_question else []),
                ],
                confidence=0.88,
                constraints=constraints,
                route=IntentRoute.BUDGET,
                reason="请求主体仅涉及预算规划或调整",
            )

        if has_plan and has_create:
            return IntentDecision(
                intent=AssistantIntent.WEEKLY_PLAN,
                operation=(
                    IntentOperation.REGENERATE
                    if has_active_plan
                    else IntentOperation.CREATE
                ),
                requires=[
                    IntentCapability.MEAL,
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.RETRIEVAL,
                    IntentCapability.VERIFIER,
                ],
                confidence=0.96,
                constraints=constraints,
                route=IntentRoute.WEEKLY_PLAN,
                reason="命中周范围和计划生成动作",
            )

        # A PlanningRequest is already scoped to weekly planning. Ambiguous commands
        # are left as consultation here; the explicit plan endpoint can safely force
        # the weekly route without weakening free-text assistant routing.
        return IntentDecision(
            intent=AssistantIntent.CONSULTATION,
            operation=IntentOperation.QUERY,
            requires=[IntentCapability.RETRIEVAL],
            confidence=0.82 if is_question or has_meal or has_shopping or has_budget else 0.58,
            constraints=constraints,
            route=IntentRoute.CONSULTATION,
            reason="未命中写操作，按只读咨询处理",
            needs_clarification=not (is_question or has_meal or has_shopping or has_budget),
        )

    @staticmethod
    def _revision_capabilities(
        has_meal: bool, has_shopping: bool, has_budget: bool
    ) -> list[IntentCapability]:
        capabilities: list[IntentCapability] = []
        if has_meal:
            capabilities.append(IntentCapability.MEAL)
        if has_shopping:
            capabilities.append(IntentCapability.SHOPPING)
        if has_budget:
            capabilities.append(IntentCapability.BUDGET)
        if not capabilities:
            capabilities.append(IntentCapability.MEAL)
        if IntentCapability.MEAL in capabilities:
            for dependent in (IntentCapability.SHOPPING, IntentCapability.BUDGET):
                if dependent not in capabilities:
                    capabilities.append(dependent)
        capabilities.append(IntentCapability.VERIFIER)
        return capabilities


intent_router = IntentRouter()

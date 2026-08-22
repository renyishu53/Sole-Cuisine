from app.ai.intent_router import intent_router
from app.schemas.intent import (
    AssistantIntent,
    IntentCapability,
    IntentOperation,
    IntentRoute,
)


def test_routes_full_weekly_plan_to_all_domain_capabilities() -> None:
    decision = intent_router.classify("生成下周每天三餐的完整周计划，预算500元")

    assert decision.intent is AssistantIntent.WEEKLY_PLAN
    assert decision.operation is IntentOperation.CREATE
    assert decision.route is IntentRoute.WEEKLY_PLAN
    assert set(decision.requires) == {
        IntentCapability.MEAL,
        IntentCapability.SHOPPING,
        IntentCapability.BUDGET,
        IntentCapability.RETRIEVAL,
        IntentCapability.VERIFIER,
    }
    assert decision.constraints["budget"] == 500


def test_weekly_plan_with_budget_but_no_meal_word_stays_weekly() -> None:
    decision = intent_router.classify("生成本周计划，预算500元")

    assert decision.intent is AssistantIntent.WEEKLY_PLAN
    assert decision.route is IntentRoute.WEEKLY_PLAN


def test_routes_shopping_only_without_running_meal_planning() -> None:
    decision = intent_router.classify("只生成本周购物清单")

    assert decision.intent is AssistantIntent.SHOPPING
    assert decision.operation is IntentOperation.CREATE
    assert IntentCapability.MEAL not in decision.requires


def test_routes_budget_only_to_budget_subgraph() -> None:
    decision = intent_router.classify("制定一个300元的预算方案")

    assert decision.intent is AssistantIntent.BUDGET
    assert decision.operation is IntentOperation.CREATE
    assert decision.requires == [IntentCapability.BUDGET]


def test_routes_existing_plan_change_to_revision_subgraph() -> None:
    decision = intent_router.classify(
        "把周三晚餐换成鸡胸肉", has_active_plan=True
    )

    assert decision.intent is AssistantIntent.PLAN_REVISION
    assert decision.operation is IntentOperation.REVISE
    assert decision.route is IntentRoute.PLAN_REVISION
    assert IntentCapability.MEAL in decision.requires
    assert IntentCapability.VERIFIER in decision.requires


def test_routes_question_to_read_only_consultation() -> None:
    decision = intent_router.classify("减脂期晚餐应该怎么吃？")

    assert decision.intent is AssistantIntent.CONSULTATION
    assert decision.operation is IntentOperation.QUERY
    assert decision.requires == [IntentCapability.RETRIEVAL]


def test_question_about_adjusting_plan_does_not_trigger_write_handoff() -> None:
    decision = intent_router.classify(
        "我应该怎么调整本周预算？", has_active_plan=True
    )

    assert decision.operation is IntentOperation.QUERY
    assert decision.intent in {AssistantIntent.BUDGET, AssistantIntent.CONSULTATION}


def test_ambiguous_request_requires_clarification() -> None:
    decision = intent_router.classify("帮帮我")

    assert decision.intent is AssistantIntent.CONSULTATION
    assert decision.needs_clarification is True
    assert decision.confidence < 0.6

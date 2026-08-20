from fastapi.testclient import TestClient

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


def test_intent_endpoint_exposes_auditable_route(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/assistant/intent",
        headers=auth_headers,
        json={"prompt": "只生成购物清单", "has_active_plan": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "shopping"
    assert response.json()["route"] == "shopping_subgraph"
    assert response.json()["handoff"]["kind"] == "shopping"
    assert response.json()["router_trace"] == ["intent", "shopping_subgraph"]


def test_consultation_branch_stays_in_chat(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/assistant/intent",
        headers=auth_headers,
        json={"prompt": "减脂期晚餐应该怎么吃？"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "consultation"
    assert body["handoff"]["kind"] == "chat"
    assert body["router_trace"] == ["intent", "consultation_subgraph"]


def test_explicit_planner_entry_skips_ambiguous_free_text_classification(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/assistant/intent",
        headers=auth_headers,
        json={
            "prompt": "按我的最新目标重新安排",
            "entry_context": "planner_generate",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "weekly_plan"
    assert body["confidence"] == 1.0
    assert body["entry_context"] == "planner_generate"
    assert body["handoff"]["mode"] == "generate"
    assert body["router_trace"] == ["intent", "weekly_plan_subgraph"]

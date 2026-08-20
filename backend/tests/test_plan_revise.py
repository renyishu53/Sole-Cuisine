"""Plan revise 端到端测试。

覆盖：
- Schema 校验（ReviseOperation 各 operation 类型）
- Demo 模式下 PlanReviseService 解析（关键词规则兜底）
- API 端到端：revise 生成预览 → confirm 派生新版本 → 对话历史持久化
- 错误码：404（计划不存在）、404（预览不存在）、400（消息非预览类型）
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# ── 辅助：准备一个已确认的 plan，返回 plan_id ──────────────────


def _prepare_confirmed_plan(
    client: TestClient, auth_headers: dict[str, str], prompt: str = "revise 测试 - 周计划"
) -> int:
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": prompt, "budget": 400},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    confirmed = client.post(
        f"/api/v1/plans/{run_id}/confirm", headers=auth_headers
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["plan_id"]


# ── Schema 校验测试 ──────────────────────────────────────────────


def test_revise_operation_replace_meal_schema() -> None:
    """replace_meal 必须包含 proposal。"""
    from app.schemas import MealProposal, ReviseOperation

    op = ReviseOperation(
        operation="replace_meal",
        target={"day": "周三", "meal_type": "晚餐"},
        constraints={"max_calories": 550},
        proposal=MealProposal(
            day="周三",
            meal_type="晚餐",
            name="香煎鸡胸肉配西兰花",
            duration=25,
            cost=18.0,
            tags=["晚餐", "高蛋白"],
            ingredients=["鸡胸肉", "西兰花", "米饭"],
        ),
        reason="用户要求周三晚餐换成鸡胸肉",
    )
    assert op.operation == "replace_meal"
    assert op.proposal is not None
    assert op.proposal.name == "香煎鸡胸肉配西兰花"


def test_revise_operation_invalid_type_raises() -> None:
    """非合法 operation 类型应被 Pydantic 拒绝。"""
    from pydantic import ValidationError

    from app.schemas import ReviseOperation

    try:
        ReviseOperation(operation="unknown_op", target={})  # type: ignore[arg-type]
        raise AssertionError("应抛 ValidationError")
    except ValidationError:
        pass


def test_meal_proposal_to_meal_item_dict_merges_meal_type_into_tags() -> None:
    """MealProposal.to_meal_item_dict 应把 meal_type 合并进 tags。"""
    from app.schemas import MealProposal

    proposal = MealProposal(
        day="周三",
        meal_type="晚餐",
        name="测试餐",
        duration=20,
        cost=10,
        tags=["高蛋白"],
        reason="",
        ingredients=["鸡胸肉"],
    )
    meal_dict = proposal.to_meal_item_dict()
    assert meal_dict["tags"][0] == "晚餐"
    assert "高蛋白" in meal_dict["tags"]
    assert meal_dict["day"] == "周三"


# ── PlanReviseService demo 模式解析测试 ───────────────────────────


def test_demo_parse_replace_meal_chicken_breast() -> None:
    """关键词规则解析：'把周三晚餐换成鸡胸肉' 应识别为 replace_meal。"""
    from app.services.plan_revise import _parse_demo_operation

    class _FakeMeal:
        day = "周三"
        name = "牛肉面"
        ingredients = ["牛肉", "面条"]

    class _FakePlan:
        meals = [_FakeMeal()]
        shopping_items: list[object] = []

    op = _parse_demo_operation("把周三晚餐换成鸡胸肉", _FakePlan())  # type: ignore[arg-type]
    assert op is not None
    assert op.operation == "replace_meal"
    assert op.target.get("day") == "周三"
    assert op.target.get("meal_type") == "晚餐"
    assert op.proposal is not None
    assert "鸡胸肉" in op.proposal.ingredients


def test_demo_parse_exclude_ingredient() -> None:
    """'购物清单里不要出现牛奶' 应识别为 exclude_ingredient。"""
    from app.services.plan_revise import _parse_demo_operation

    class _FakePlan:
        meals: list[object] = []
        shopping_items: list[object] = []

    op = _parse_demo_operation("购物清单里不要出现牛奶", _FakePlan())  # type: ignore[arg-type]
    assert op is not None
    assert op.operation == "exclude_ingredient"
    assert op.target.get("ingredient") == "牛奶"


def test_demo_parse_update_budget() -> None:
    """'总预算降到 300 元' 应识别为 update_budget，提取 300。"""
    from app.services.plan_revise import _parse_demo_operation

    class _FakePlan:
        meals: list[object] = []
        shopping_items: list[object] = []

    op = _parse_demo_operation("总预算降到 300 元", _FakePlan())  # type: ignore[arg-type]
    assert op is not None
    assert op.operation == "update_budget"
    assert op.target.get("budget_limit") == 300.0


def test_demo_parse_shopping_adjustment() -> None:
    """购物清单增删改应进入独立的购物调整操作。"""
    from app.services.plan_revise import _parse_demo_operation

    class _FakePlan:
        meals: list[object] = []
        shopping_items: list[object] = []

    op = _parse_demo_operation(
        "购物清单添加鸡胸肉2斤，价格30元", _FakePlan()  # type: ignore[arg-type]
    )
    assert op is not None
    assert op.operation == "adjust_shopping"
    assert op.target["action"] == "add"
    assert op.target["name"] == "鸡胸肉"
    assert op.target["quantity"] == "2斤"


# ── API 端到端测试 ────────────────────────────────────────────────


def test_revise_returns_preview_with_diff(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /plans/{id}/revise 返回 before/after/diff 预览。"""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    response = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "把周三晚餐换成鸡胸肉"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan_id"] == plan_id
    assert body["operation"]["operation"] == "replace_meal"
    assert body["operation"]["target"]["day"] == "周三"
    assert body["operation"]["proposal"]["name"]
    assert body["routing"]["route"] == "meal_revision_subgraph"
    assert set(body["routing"]["requires"]) == {
        "meal", "shopping", "budget", "verifier"
    }
    # before/after 必须有 meals + nutrition + budget
    assert len(body["before"]["meals"]) > 0
    assert len(body["after"]["meals"]) > 0
    assert "calories" in body["before"]["nutrition"]
    assert "calories" in body["after"]["nutrition"]
    # diff 应有 changed_meals
    assert any("鸡胸肉" in s or "→" in s for s in body["diff"]["changed_meals"])
    assert any("购物项" in s for s in body["diff"]["changed_shopping"])
    # revise_id 必须返回（消息 ID）
    assert body["revise_id"]
    assert body["message_id"]
    assert body["can_confirm"] is True


def test_macro_target_preview_cannot_confirm_unchanged_plan(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A recorded nutrition intent must not create an identical plan version."""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    preview = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "蛋白质提高到每天120g"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["operation"]["operation"] == "adjust_macro_target"
    assert body["can_confirm"] is False

    confirm = client.post(
        f"/api/v1/plans/{plan_id}/revise/{body['revise_id']}/confirm",
        headers=auth_headers,
    )
    assert confirm.status_code == 409


def test_shopping_revision_uses_shopping_branch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    preview = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "购物清单添加鸡胸肉2斤，价格30元"},
    )

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["operation"]["operation"] == "adjust_shopping"
    assert body["routing"]["route"] == "shopping_revision_subgraph"
    assert body["routing"]["requires"] == ["shopping", "budget", "verifier"]
    assert body["can_confirm"] is True
    assert any("鸡胸肉" in item for item in body["diff"]["changed_shopping"])


def test_revise_404_when_plan_missing(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """不存在的 plan_id 应返回 404。"""
    response = client.post(
        "/api/v1/plans/99999/revise",
        headers=auth_headers,
        json={"message": "随便改改"},
    )
    assert response.status_code == 404


def test_revise_confirm_derives_new_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """确认修改应派生带 parent_plan_id 的新版本，版本号 +1。"""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    # 先 GET /plans/{id} 拿当前版本号
    before = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
    assert before.status_code == 200
    before_version = before.json()["version"]

    # 提交修改
    revise = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "把周三晚餐换成鸡胸肉"},
    )
    assert revise.status_code == 200, revise.text
    revise_id = revise.json()["revise_id"]

    # 确认
    confirm = client.post(
        f"/api/v1/plans/{plan_id}/revise/{revise_id}/confirm",
        headers=auth_headers,
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["plan_id"] == plan_id
    assert body["new_plan_id"] != plan_id
    assert body["new_version"] == before_version + 1
    assert body["parent_plan_id"] == plan_id

    # 新版本应有 meals，且第一餐应是鸡胸肉主题
    new_plan = client.get(
        f"/api/v1/plans/{body['new_plan_id']}", headers=auth_headers
    )
    assert new_plan.status_code == 200
    assert new_plan.json()["parent_plan_id"] == plan_id
    assert len(new_plan.json()["meals"]) > 0


def test_revise_confirm_404_when_preview_missing(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """不存在的 revise_id 应返回 404。"""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    confirm = client.post(
        f"/api/v1/plans/{plan_id}/revise/99999/confirm",
        headers=auth_headers,
    )
    assert confirm.status_code == 404


def test_revise_creates_chat_session_with_history(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """revise 应创建对话会话，且消息持久化，可通过 chat sessions API 列出。"""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    revise = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "把周三晚餐换成鸡胸肉"},
    )
    assert revise.status_code == 200
    # 多轮修改：第二条消息应复用同一会话
    revise2 = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "总预算降到 350 元"},
    )
    assert revise2.status_code == 200

    # 列出对话会话
    sessions = client.get("/api/v1/chat/sessions", headers=auth_headers)
    assert sessions.status_code == 200
    body = sessions.json()
    assert len(body) > 0
    # 应有一个标题以 [计划v 开头的会话
    plan_sessions = [s for s in body if s["title"].startswith("[计划v")]
    assert plan_sessions, "应创建以 [计划v 开头的对话会话"


def test_revise_exclude_ingredient_end_to_end(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """'不要牛奶' 端到端：从所有餐食食材与购物清单移除牛奶。"""
    plan_id = _prepare_confirmed_plan(client, auth_headers)
    response = client.post(
        f"/api/v1/plans/{plan_id}/revise",
        headers=auth_headers,
        json={"message": "购物清单里不要出现牛奶"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"]["operation"] == "exclude_ingredient"
    assert body["operation"]["target"]["ingredient"] == "牛奶"
    # diff 应提示购物清单移除
    assert any("牛奶" in s for s in body["diff"]["changed_shopping"])

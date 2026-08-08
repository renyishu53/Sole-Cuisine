import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.api.router as api_router
from app.core.config import Settings


def _register_second_user(
    client: TestClient, *, phone: str, display_name: str = "隔离用户"
) -> dict[str, str]:
    """注册第二个用户用于 cross-user 隔离测试，返回 Authorization headers。"""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "verification_code": "123456",
            "password": "solochef-test",
            "display_name": display_name,
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_business_endpoint_requires_jwt(client: TestClient) -> None:
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 401


def test_registration_creates_user(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["display_name"] == "测试用户"
    assert payload["user"]["phone"] == "13800000001"


def test_dashboard_uses_authenticated_user(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/dashboard", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["user_name"] == "测试用户"


def test_refresh_token_rotates_session(client: TestClient, auth_session: dict[str, object]) -> None:
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth_session["refresh_token"]},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] != auth_session["access_token"]
    reused = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth_session["refresh_token"]},
    )
    assert reused.status_code == 401


def test_sms_auth_requires_code_and_logs_in_existing_user(
    client: TestClient, auth_session: dict[str, object]
) -> None:
    missing_code = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000088",
            "password": "solochef-test",
            "display_name": "验证码用户",
        },
    )
    assert missing_code.status_code == 422

    wrong_code = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000088",
            "verification_code": "000000",
            "password": "solochef-test",
            "display_name": "验证码用户",
        },
    )
    assert wrong_code.status_code == 422

    sms_login = client.post(
        "/api/v1/auth/sms/login",
        json={"phone": "13800000001", "code": "123456"},
    )
    assert sms_login.status_code == 200
    assert sms_login.json()["user"]["phone"] == "13800000001"


def test_generate_weekly_plan_and_trace_are_user_scoped(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "孩子不吃辣，周三要快手菜，安排一周晚餐", "budget": 500},
    )
    assert response.status_code == 201
    payload = response.json()
    assert len(payload["meals"]) == 7
    assert payload["budget"]["estimated"] <= 500
    trace = client.get(f"/api/v1/agents/runs/{payload['run_id']}", headers=auth_headers)
    assert trace.status_code == 200
    assert len(trace.json()["steps"]) == 11
    assert any(step["name"] == "meal_agent" for step in trace.json()["steps"])
    assert any(step["name"] == "domain_coordinator" for step in trace.json()["steps"])
    assert payload["domain"]["meal"]["strategy"]

    other_headers = _register_second_user(client, phone="13800000011", display_name="追踪隔离用户")
    cross_user_trace = client.get(
        f"/api/v1/agents/runs/{payload['run_id']}", headers=other_headers
    )
    assert cross_user_trace.status_code == 404


def test_demo_mode_rejects_real_llm_smoke(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api_router,
        "settings",
        Settings(_env_file=None, llm_provider="demo", llm_api_key=""),
    )
    response = client.post("/api/v1/ai/llm/smoke", headers=auth_headers)
    assert response.status_code == 503
    assert "未配置真实" in response.json()["detail"]


def test_agent_run_is_persisted_to_database(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Agent run should be readable from DB after generation."""
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "持久化测试 - 安排一周晚餐", "budget": 500},
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    trace = client.get(f"/api/v1/agents/runs/{run_id}", headers=auth_headers)
    assert trace.status_code == 200
    assert trace.json()["id"] == run_id

    runs = client.get("/api/v1/agents/runs", headers=auth_headers)
    assert runs.status_code == 200
    assert any(r["id"] == run_id for r in runs.json())


def test_failed_agent_run_is_persisted(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingWorkflow:
        _generator = type("Generator", (), {"mode": "test"})()

        def set_checkpointer(self, checkpointer: object) -> None:
            del checkpointer

        async def run(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("intentional workflow failure")

    monkeypatch.setattr(api_router.planning_service, "_workflow", FailingWorkflow())
    with pytest.raises(RuntimeError, match="intentional workflow failure"):
        client.post(
            "/api/v1/plans/generate-weekly",
            headers=auth_headers,
            json={"prompt": "失败运行持久化测试", "budget": 500},
        )

    runs = client.get("/api/v1/agents/runs", headers=auth_headers)
    failed = next(item for item in runs.json() if item["request"] == "失败运行持久化测试")
    assert failed["status"] == "failed"
    assert failed["error_type"] == "RuntimeError"
    assert failed["finished_at"] is not None

    detail = client.get(f"/api/v1/agents/runs/{failed['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["error_message"] == "intentional workflow failure"


def test_plan_confirmation_writes_to_database(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Confirming a plan should persist meals and shopping (budget lives on the plan row)."""
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "确认测试 - 快手菜", "budget": 400},
    )
    run_id = response.json()["run_id"]
    confirmed = client.post(f"/api/v1/plans/{run_id}/confirm", headers=auth_headers)
    assert confirmed.status_code == 200
    plan_id = confirmed.json()["plan_id"]

    plan = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
    assert plan.status_code == 200
    assert plan.json()["status"] == "confirmed"
    assert len(plan.json()["meals"]) > 0

    meals = client.get("/api/v1/meals", headers=auth_headers)
    assert meals.status_code == 200
    assert meals.json() == plan.json()["meals"]


def test_confirm_plan_is_idempotent(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Duplicate confirm should return the same plan_id."""
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "幂等测试 - 安排一周快餐", "budget": 300},
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    first = client.post(f"/api/v1/plans/{run_id}/confirm", headers=auth_headers)
    assert first.status_code == 200
    second = client.post(f"/api/v1/plans/{run_id}/confirm", headers=auth_headers)
    assert second.status_code == 200
    assert first.json()["plan_id"] == second.json()["plan_id"]
    assert "已存在" in second.json()["message"]


def test_plan_versions_activate_and_rollback(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first_run = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "计划版本一测试", "budget": 410},
    ).json()["run_id"]
    first_plan = client.post(f"/api/v1/plans/{first_run}/confirm", headers=auth_headers).json()[
        "plan_id"
    ]
    second_run = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "计划版本二测试", "budget": 420},
    ).json()["run_id"]
    second_plan = client.post(f"/api/v1/plans/{second_run}/confirm", headers=auth_headers).json()[
        "plan_id"
    ]

    versions = client.get(f"/api/v1/plans/{second_plan}/versions", headers=auth_headers).json()
    first = next(item for item in versions if item["id"] == first_plan)
    second = next(item for item in versions if item["id"] == second_plan)
    assert second["version"] == first["version"] + 1
    assert second["parent_plan_id"] == first_plan
    assert second["is_active"] is True
    assert first["is_active"] is False

    activated = client.post(f"/api/v1/plans/{first_plan}/activate", headers=auth_headers)
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    client.post(f"/api/v1/plans/{second_plan}/activate", headers=auth_headers)
    rolled_back = client.post(f"/api/v1/plans/{second_plan}/rollback", headers=auth_headers)
    assert rolled_back.status_code == 200
    assert rolled_back.json()["id"] == first_plan
    assert rolled_back.json()["is_active"] is True

    other_headers = _register_second_user(client, phone="13800000012", display_name="版本隔离用户")
    assert (
        client.get(f"/api/v1/plans/{second_plan}/versions", headers=other_headers).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/plans/{second_plan}/activate", headers=other_headers).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/plans/{second_plan}/rollback", headers=other_headers).status_code
        == 404
    )


def test_plan_cross_user_isolation(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Plans from one user should not be visible to another."""
    response = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "跨用户隔离", "budget": 300},
    )
    assert response.status_code == 201
    confirmed = client.post(
        f"/api/v1/plans/{response.json()['run_id']}/confirm", headers=auth_headers
    )
    assert confirmed.status_code == 200
    plan_id = confirmed.json()["plan_id"]

    other_headers = _register_second_user(client, phone="13800000013", display_name="隔离测试用户")
    hidden = client.get(f"/api/v1/plans/{plan_id}", headers=other_headers)
    assert hidden.status_code == 404


def test_plan_derive_diff_and_checkpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    generated = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "派生与断点测试", "budget": 460},
    )
    assert generated.status_code == 201
    run_id = generated.json()["run_id"]
    detail = client.get(f"/api/v1/agents/runs/{run_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["checkpoint"]["last_completed_node"]

    confirmed = client.post(f"/api/v1/plans/{run_id}/confirm", headers=auth_headers)
    assert confirmed.status_code == 200
    source_id = confirmed.json()["plan_id"]
    derived = client.post(f"/api/v1/plans/{source_id}/derive", headers=auth_headers)
    assert derived.status_code == 200
    assert derived.json()["parent_plan_id"] == source_id
    assert derived.json()["version"] > 1
    compared = client.get(
        f"/api/v1/plans/{source_id}/diff/{derived.json()['id']}", headers=auth_headers
    )
    assert compared.status_code == 200
    # Phase 3 清理：plan_tasks 表已删除，diff 仅保留 meals / shopping 两段。
    assert set(compared.json()["sections"]) == {"meals", "shopping"}


def test_chat_session_search_rename_and_delete(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "暑期安排检索目标"},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    search = client.get("/api/v1/chat/sessions", headers=auth_headers, params={"query": "检索目标"})
    assert any(item["id"] == session_id for item in search.json())
    renamed = client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "暑期安排已重命名"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "暑期安排已重命名"
    deleted = client.delete(f"/api/v1/chat/sessions/{session_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/v1/chat/sessions/{session_id}", headers=auth_headers).status_code == 404
    )


def test_background_file_and_graph_jobs_are_enqueued(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    sent: list[tuple[str, list[str]]] = []

    def send_task(name: str, args: list[str], **kwargs: object) -> None:
        del kwargs
        sent.append((name, args))

    monkeypatch.setattr(api_router.celery_app, "send_task", send_task)
    uploaded = client.post(
        "/api/v1/knowledge/jobs/upload",
        headers=auth_headers,
        files={"file": ("solo.txt", "日常约束：不吃辣".encode(), "text/plain")},
        data={"category": "日常知识"},
    )
    assert uploaded.status_code == 202
    assert uploaded.json()["kind"] == "knowledge_file"
    graph = client.post("/api/v1/knowledge/jobs/graph-sync", headers=auth_headers)
    assert graph.status_code == 202
    assert [item[0] for item in sent] == [
        "solochef.process_knowledge_file",
        "solochef.sync_member_graph",
    ]


def test_domain_crud_is_persisted_and_user_scoped(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    headers = _register_second_user(client, phone="13800000014", display_name="领域 CRUD 用户")

    assert client.get("/api/v1/meals", headers=headers).json() == []
    assert client.get("/api/v1/shopping", headers=headers).json() == []

    meal = client.post(
        "/api/v1/meals",
        headers=headers,
        json={
            "day": "周一",
            "name": "番茄鸡蛋面",
            "duration": 20,
            "cost": 18,
            "tags": ["快手"],
            "reason": "手工安排",
            "ingredients": ["番茄", "鸡蛋"],
        },
    )
    assert meal.status_code == 201
    meal_id = meal.json()["id"]
    updated_meal = client.patch(f"/api/v1/meals/{meal_id}", headers=headers, json={"cost": 20})
    assert updated_meal.status_code == 200
    assert updated_meal.json()["cost"] == 20

    shopping = client.post(
        "/api/v1/shopping",
        headers=headers,
        json={"name": "鸡蛋", "category": "肉蛋奶", "quantity": "12 个", "price": 12},
    )
    assert shopping.status_code == 201
    shopping_id = shopping.json()["id"]
    checked = client.patch(
        f"/api/v1/shopping/{shopping_id}", headers=headers, json={"purchased": True}
    )
    assert checked.status_code == 200
    assert checked.json()["purchased"] is True

    # Phase 3 清理：/tasks 与 /budget (PATCH) 端点随 plan_tasks / plan_budgets 表一并移除，
    # 预算改由 WeeklyPlan.budget 标量列承载，不再单独 CRUD。

    assert client.get(f"/api/v1/meals/{meal_id}", headers=auth_headers).status_code == 404
    assert client.delete(f"/api/v1/meals/{meal_id}", headers=headers).status_code == 204
    assert client.delete(f"/api/v1/shopping/{shopping_id}", headers=headers).status_code == 204


def test_domain_operations_close_the_household_loop(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    run = client.post(
        "/api/v1/plans/generate-weekly",
        headers=auth_headers,
        json={"prompt": "生成用于业务闭环测试的一周计划", "budget": 500},
    )
    assert run.status_code == 201
    confirmed = client.post(f"/api/v1/plans/{run.json()['run_id']}/confirm", headers=auth_headers)
    assert confirmed.status_code == 200

    meal = client.get("/api/v1/meals", headers=auth_headers).json()[0]
    replaced = client.post(
        f"/api/v1/meals/{meal['id']}/replace",
        headers=auth_headers,
        json={"feedback": "换成清淡快手餐，不要花生"},
    )
    assert replaced.status_code == 200
    assert "花生" not in replaced.json()["meal"]["ingredients"]

    duplicate = client.post(
        "/api/v1/shopping",
        headers=auth_headers,
        json={
            "name": "鸡蛋",
            "category": "肉蛋奶",
            "quantity": "6 个",
            "price": 8,
            "source": "补充采购",
        },
    )
    assert duplicate.status_code == 201
    client.post(
        "/api/v1/shopping",
        headers=auth_headers,
        json={
            "name": " 鸡 蛋 ",
            "category": "肉蛋奶",
            "quantity": "12 个",
            "price": 12,
            "source": "早餐",
        },
    )
    merged = client.post("/api/v1/shopping/merge", headers=auth_headers)
    assert merged.status_code == 200
    assert merged.json()["merged_groups"] >= 1
    assert merged.json()["removed_items"] >= 1

    expense = client.post(
        "/api/v1/budget/expenses",
        headers=auth_headers,
        json={
            "category": "餐食",
            "amount": 450,
            "occurred_at": "2026-08-04T12:00:00+08:00",
            "note": "本周采购",
        },
    )
    assert expense.status_code == 201
    analytics = client.get("/api/v1/budget/analytics", headers=auth_headers)
    assert analytics.status_code == 200
    assert analytics.json()["actual_spent"] == 450
    assert analytics.json()["warning"] is True
    assert (
        client.delete(
            f"/api/v1/budget/expenses/{expense.json()['id']}", headers=auth_headers
        ).status_code
        == 204
    )


def test_recipe_and_chat_are_user_scoped(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    recipe = client.post(
        "/api/v1/recipes",
        headers=auth_headers,
        json={
            "name": "番茄面",
            "ingredients": ["番茄", "面条"],
            "steps": ["煮面", "加入番茄"],
            "tags": ["快手"],
            "allergens": [],
            "duration": 20,
            "estimated_cost": 18,
        },
    )
    assert recipe.status_code == 201

    chat = client.post("/api/v1/chat/sessions", headers=auth_headers, json={"title": "周计划调整"})
    assert chat.status_code == 201
    session_id = chat.json()["id"]
    with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/messages/stream",
        headers=auth_headers,
        json={"content": "把本周计划调整得更清淡一些", "budget": 500},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: step" in body
    assert "domain_coordinator" in body
    assert "event: complete" in body
    assert "id: " in body
    detail = client.get(f"/api/v1/chat/sessions/{session_id}", headers=auth_headers)
    assert [message["role"] for message in detail.json()["messages"]] == [
        "user",
        "assistant",
    ]

    replay = client.get(f"/api/v1/chat/sessions/{session_id}/events", headers=auth_headers)
    assert replay.status_code == 200
    assert "events" in replay.json()
    assert "turn_status" in replay.json()

    other_headers = _register_second_user(client, phone="13800000016", display_name="对话隔离用户")
    hidden_chat = client.get(f"/api/v1/chat/sessions/{session_id}", headers=other_headers)
    assert hidden_chat.status_code == 404
    assert client.get("/api/v1/recipes", headers=other_headers).json() == []


def test_background_job_enqueue_and_user_scope(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    sent: list[tuple[str, list[str], str]] = []

    def fake_send_task(name: str, args: list[str], task_id: str) -> None:
        sent.append((name, args, task_id))

    monkeypatch.setattr(api_router.celery_app, "send_task", fake_send_task)
    queued = client.post(
        "/api/v1/knowledge/jobs/text",
        headers=auth_headers,
        json={
            "name": "后台知识",
            "category": "日常知识",
            "content": "这是一段足够长的日常知识内容，用于验证 Celery 后台任务入队。",
        },
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert sent[0][0] == "solochef.process_knowledge_text"
    assert sent[0][2] == queued.json()["id"]

    other_headers = _register_second_user(client, phone="13800000017", display_name="任务隔离用户")
    hidden = client.get(f"/api/v1/jobs/{queued.json()['id']}", headers=other_headers)
    assert hidden.status_code == 404


def test_reset_password_via_sms_code(client: TestClient) -> None:
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000055",
            "verification_code": "123456",
            "password": "original-pass",
            "display_name": "重置用户",
        },
    )
    assert registered.status_code == 201

    unregistered = client.post(
        "/api/v1/auth/password/reset",
        json={"phone": "13800000099", "code": "123456", "new_password": "brand-new-pass"},
    )
    assert unregistered.status_code == 404

    wrong_code = client.post(
        "/api/v1/auth/password/reset",
        json={"phone": "13800000055", "code": "000000", "new_password": "brand-new-pass"},
    )
    assert wrong_code.status_code == 422

    reset = client.post(
        "/api/v1/auth/password/reset",
        json={"phone": "13800000055", "code": "123456", "new_password": "brand-new-pass"},
    )
    assert reset.status_code == 204

    old_login = client.post(
        "/api/v1/auth/login", json={"phone": "13800000055", "password": "original-pass"}
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/v1/auth/login", json={"phone": "13800000055", "password": "brand-new-pass"}
    )
    assert new_login.status_code == 200

    revoked = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": registered.json()["refresh_token"]}
    )
    assert revoked.status_code == 401


def test_device_sessions_listing_and_revocation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    listed = client.get("/api/v1/auth/sessions", headers=auth_headers)
    assert listed.status_code == 200
    sessions = listed.json()
    assert len(sessions) >= 1
    assert sessions[0]["id"]

    revoked = client.delete(f"/api/v1/auth/sessions/{sessions[0]['id']}", headers=auth_headers)
    assert revoked.status_code == 204
    remaining = client.get("/api/v1/auth/sessions", headers=auth_headers).json()
    assert all(item["id"] != sessions[0]["id"] for item in remaining)

    foreign = client.delete("/api/v1/auth/sessions/not-a-session", headers=auth_headers)
    assert foreign.status_code == 404


# ---------- 用户画像与营养目标闭环 ----------


def test_get_profile_auto_creates_with_defaults(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """GET /profile 不存在时自动创建默认画像。"""
    response = client.get("/api/v1/profile", headers=auth_headers)
    assert response.status_code == 200
    profile = response.json()
    assert profile["height_cm"] == 170.0
    assert profile["weight_kg"] == 65.0
    assert profile["age"] == 30
    assert profile["gender"] == "male"
    assert profile["activity_level"] == "moderate"
    assert profile["goal_type"] == "maintain"


def test_update_profile_persists_fields(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """PUT /profile 更新画像字段后再次 GET 返回新值。"""
    updated = client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={
            "height_cm": 178.0,
            "weight_kg": 72.5,
            "age": 28,
            "gender": "male",
            "activity_level": "active",
            "goal_type": "bulk",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["height_cm"] == 178.0
    assert updated.json()["goal_type"] == "bulk"

    fetched = client.get("/api/v1/profile", headers=auth_headers)
    assert fetched.json()["weight_kg"] == 72.5
    assert fetched.json()["activity_level"] == "active"


def test_update_profile_rejects_invalid_values(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """PUT /profile 对超范围值返回 422。"""
    invalid = client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={"height_cm": 300.0, "age": 200},
    )
    assert invalid.status_code == 422


def test_update_profile_requires_at_least_one_field(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """PUT /profile 空请求体返回 422。"""
    empty = client.put("/api/v1/profile", headers=auth_headers, json={})
    assert empty.status_code == 422


def test_compute_nutrition_goal_male_bulk(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /profile/nutrition-goal 按 Mifflin-St Jeor 计算 TDEE 并持久化。"""
    client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={
            "height_cm": 180.0,
            "weight_kg": 75.0,
            "age": 25,
            "gender": "male",
            "activity_level": "moderate",
            "goal_type": "bulk",
        },
    )
    response = client.post("/api/v1/profile/nutrition-goal", headers=auth_headers)
    assert response.status_code == 201
    goal = response.json()
    # BMR = 10*75 + 6.25*180 - 5*25 + 5 = 750 + 1125 - 125 + 5 = 1755
    assert goal["bmr"] == 1755.0
    # TDEE = 1755 * 1.55 = 2720.25
    assert goal["tdee"] == 2720.2
    # target = round(2720.25 * 1.10, 1) = 2992.3（浮点舍入）
    assert goal["target_calories"] == 2992.3
    # 增肌比例 P30% C40% F30%
    assert goal["protein_g"] == 224.4  # 2992.3 * 0.30 / 4
    assert goal["carb_g"] == 299.2  # 2992.3 * 0.40 / 4
    assert goal["fat_g"] == 99.7  # 2992.3 * 0.30 / 9


def test_compute_nutrition_goal_female_cut(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """女性减脂目标：BMR 用 -161 偏移，热量赤字 15%。"""
    client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={
            "height_cm": 165.0,
            "weight_kg": 58.0,
            "age": 30,
            "gender": "female",
            "activity_level": "light",
            "goal_type": "cut",
        },
    )
    response = client.post("/api/v1/profile/nutrition-goal", headers=auth_headers)
    assert response.status_code == 201
    goal = response.json()
    # BMR = 10*58 + 6.25*165 - 5*30 - 161 = 580 + 1031.25 - 150 - 161 = 1300.25
    assert goal["bmr"] == 1300.2
    # TDEE = 1300.25 * 1.375 = 1787.84...
    assert goal["tdee"] == 1787.8
    # target = 1787.84 * 0.85 = 1519.67 → 1519.7
    assert goal["target_calories"] == 1519.7
    # 减脂比例 P40% C30% F30%
    assert goal["protein_g"] == 152.0  # 1519.7 * 0.40 / 4
    assert goal["fat_g"] == 50.7  # 1519.7 * 0.30 / 9


def test_get_nutrition_goal_returns_404_when_not_computed(
    client: TestClient
) -> None:
    """GET /profile/nutrition-goal 未计算时返回 404（用新用户确保无残留）。"""
    headers = _register_second_user(client, phone="13900000077", display_name="无目标用户")
    response = client.get("/api/v1/profile/nutrition-goal", headers=headers)
    assert response.status_code == 404


def test_get_nutrition_goal_after_compute(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """计算后 GET 返回持久化的目标。"""
    client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={"height_cm": 175.0, "weight_kg": 70.0, "age": 30, "gender": "male"},
    )
    client.post("/api/v1/profile/nutrition-goal", headers=auth_headers)
    response = client.get("/api/v1/profile/nutrition-goal", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["bmr"] > 0
    assert response.json()["tdee"] > response.json()["bmr"]


def test_compute_nutrition_goal_is_idempotent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """重复计算覆盖旧目标（user_id 唯一约束）。"""
    client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={"goal_type": "maintain", "activity_level": "sedentary"},
    )
    first = client.post("/api/v1/profile/nutrition-goal", headers=auth_headers)
    assert first.status_code == 201

    client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={"goal_type": "bulk", "activity_level": "active"},
    )
    second = client.post("/api/v1/profile/nutrition-goal", headers=auth_headers)
    assert second.status_code == 201
    assert second.json()["target_calories"] > first.json()["target_calories"]


def test_profile_is_user_scoped(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """用户 A 的画像与用户 B 隔离。"""
    client.put(
        "/api/v1/profile", headers=auth_headers, json={"height_cm": 190.0}
    )
    other = _register_second_user(client, phone="13900000099")
    other_profile = client.get("/api/v1/profile", headers=other)
    assert other_profile.json()["height_cm"] == 170.0  # 默认值，非 190


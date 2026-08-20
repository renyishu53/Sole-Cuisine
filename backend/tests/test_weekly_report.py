"""阶段5：忌口自动纳入 + 阶段性一周报告。

测试走 demo 模式（无外部 LLM / Neo4j / 向量库），反馈回流降级为未同步标记。
使用独立注册用户，避免与其他测试共享内存库中的餐食/反馈相互污染。
"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.repositories.planning import current_week_start_utc


def _register(client: TestClient, phone: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "verification_code": "123456",
            "password": "solochef-p5",
            "display_name": "P5 隔离用户",
        },
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_meal(
    client: TestClient, headers: dict[str, str], day: str, name: str
) -> dict:
    resp = client.post(
        "/api/v1/meals",
        headers=headers,
        json={"day": day, "name": name, "ingredients": ["鸡胸肉"], "cost": 28},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_constraint_auto_inclusion_after_three_consecutive_negative(
    client: TestClient,
) -> None:
    """某食材标签连续 3 次负反馈后自动纳入忌口并写入画像。"""
    headers = _register(client, "13800002001")
    # 确保画像存在（忌口写入依赖 UserProfile）
    assert client.get("/api/v1/profile", headers=headers).status_code == 200
    meal = _create_meal(client, headers, "周一", "麻辣香锅")

    for _ in range(3):
        resp = client.post(
            f"/api/v1/meals/{meal['id']}/checkin",
            headers=headers,
            json={
                "eaten": False,
                "deviation_type": "no_appetite",
                "deviation_reason": "太辣了，不想吃",
            },
        )
        assert resp.status_code == 200, resp.text

    profile = client.get("/api/v1/profile", headers=headers).json()
    assert "辣" in profile["constraints"]


def test_weekly_report_returns_empty_state_without_current_plan(
    client: TestClient,
) -> None:
    """No current-week plan must not produce default metrics or advice."""
    headers = _register(client, "13800002002")

    resp = client.get("/api/v1/reports/weekly", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["has_data"] is False
    assert body["achievements"] == []
    assert body["suggestions"] == []
    assert body["coverage"]["meal_planned"] == 0


def test_weekly_report_defaults_to_current_week_plan(client: TestClient) -> None:
    """A confirmed plan created this week is reported by the default endpoint."""
    headers = _register(client, "13800002003")
    meal = _create_meal(client, headers, "周一", "清蒸鲈鱼")
    client.post(f"/api/v1/meals/{meal['id']}/checkin", headers=headers, json={"eaten": True})

    resp = client.get("/api/v1/reports/weekly", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["has_data"] is True
    assert body["coverage"]["meal_planned"] == 1
    assert body["week_label"]

    # The current week is still in progress. It must not be treated as a
    # completed retrospective or produce premature next-week recommendations.
    assert body["suggestions"] == []

    periods = client.get("/api/v1/reports/weekly/periods", headers=headers)
    assert periods.status_code == 200, periods.text
    assert len(periods.json()) == 1
    selected = client.get(
        "/api/v1/reports/weekly",
        headers=headers,
        params={"week_start": periods.json()[0]["week_start"]},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["has_data"] is True

"""阶段4（打卡）：餐食"已吃"打卡 + 未吃偏差回流 + 今日营养进度聚合。

测试走 demo 模式，无外部 LLM / Neo4j / 向量库依赖（feedback 回流降级为
未同步标记）。使用独立注册用户，避免与其他测试共享内存库中的餐食相互污染。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _register(client: TestClient, phone: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "verification_code": "123456",
            "password": "solochef-p4",
            "display_name": "P4 隔离用户",
        },
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_recipe(
    client: TestClient, headers: dict[str, str], name: str, ingredients: list[str]
) -> None:
    # 注意：POST /recipes 会忽略传入的 nutrition，改为按 ingredients 重新估算，
    # 因此这里只需保证名称/食材与餐食一致即可触发菜谱校准。
    resp = client.post(
        "/api/v1/recipes",
        headers=headers,
        json={"name": name, "ingredients": ingredients, "servings": 2},
    )
    assert resp.status_code == 201, resp.text


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


def test_meal_checkin_marks_eaten_and_clears_deviation(
    client: TestClient,
) -> None:
    headers = _register(client, "13800001001")
    meal = _create_meal(client, headers, "周一", "香煎鸡胸肉")

    resp = client.post(
        f"/api/v1/meals/{meal['id']}/checkin",
        headers=headers,
        json={"eaten": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eaten"] is True
    assert body["eaten_at"] is not None
    assert body["deviation_type"] is None
    assert body["deviation_reason"] == ""


def test_meal_checkin_records_deviation_when_not_eaten(
    client: TestClient,
) -> None:
    headers = _register(client, "13800001002")
    meal = _create_meal(client, headers, "周二", "清蒸鲈鱼")

    resp = client.post(
        f"/api/v1/meals/{meal['id']}/checkin",
        headers=headers,
        json={"eaten": False, "deviation_type": "not_available", "deviation_reason": "菜市场没买到鲈鱼"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eaten"] is False
    assert body["eaten_at"] is None
    assert body["deviation_type"] == "not_available"
    assert "鲈鱼" in body["deviation_reason"]


def test_today_nutrition_aggregates_eaten_meals(
    client: TestClient,
) -> None:
    headers = _register(client, "13800001003")
    today_label = _WEEKDAY_CN[datetime.now(UTC).weekday()]
    _create_recipe(client, headers, "香煎鸡胸肉", ["鸡胸肉"])
    meal = _create_meal(client, headers, today_label, "香煎鸡胸肉")
    # 已吃后计入今日进度
    client.post(
        f"/api/v1/meals/{meal['id']}/checkin",
        headers=headers,
        json={"eaten": True},
    )

    resp = client.get("/api/v1/meals/today/nutrition", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["day"] == today_label
    assert body["eaten_count"] >= 1
    calories = body["nutrients"]["calories"]
    assert calories["consumed"] > 0
    assert calories["target"] > 0

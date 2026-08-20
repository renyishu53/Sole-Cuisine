"""G07 食材替换营养联动：替换后重算单餐/全天营养差异并联动购物清单。

测试强制 demo 模式（不调用 LLM），替换走确定性 fallback，结果可复现。
使用独立注册用户，避免与其他测试共享内存库中的菜谱/餐食相互污染。
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services import domain as domain_svc


def _register(client: TestClient, phone: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "verification_code": "123456",
            "password": "solochef-g07",
            "display_name": "G07 隔离用户",
        },
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_meal(
    client: TestClient, headers: dict[str, str], day: str, name: str, ingredients: list[str]
) -> dict:
    resp = client.post(
        "/api/v1/meals",
        headers=headers,
        json={"day": day, "name": name, "ingredients": ingredients, "cost": 38},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_shopping(
    client: TestClient, headers: dict[str, str], name: str, category: str, source: str
) -> None:
    resp = client.post(
        "/api/v1/shopping",
        headers=headers,
        json={"name": name, "category": category, "quantity": "1 份", "source": source},
    )
    assert resp.status_code == 201, resp.text


def _create_recipe(
    client: TestClient, headers: dict[str, str], name: str, ingredients: list[str], nutrition: dict
) -> None:
    resp = client.post(
        "/api/v1/recipes",
        headers=headers,
        json={"name": name, "ingredients": ingredients, "servings": 2, "nutrition": nutrition},
    )
    assert resp.status_code == 201, resp.text


def test_meal_replacement_recomputes_nutrition_and_links_shopping(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 强制 demo 模式，替换走确定性 fallback，避免测试依赖外部 LLM
    monkeypatch.setattr(
        domain_svc.domain_operations_service, "_settings", Settings(llm_provider="demo")
    )
    headers = _register(client, "13800000999")

    # 1. 原始餐食（无匹配菜谱 → 未校准）与对应购物项
    meal = _create_meal(client, headers, "周一", "菌菇鸡肉焖饭", ["鸡肉", "菌菇", "大米"])
    meal_id = meal["id"]
    _create_shopping(client, headers, "鸡肉", "肉蛋奶", "周一")
    _create_shopping(client, headers, "菌菇", "蔬菜", "周一")
    _create_shopping(client, headers, "大米", "主食", "周一")

    # 2. 供 fallback 命中的替换菜谱（2 人份营养，单份会被 1/servings 折算）
    _create_recipe(
        client,
        headers,
        "番茄鸡蛋面",
        ["番茄", "鸡蛋", "面条"],
        {"calories": 450.0, "protein_g": 22.0, "fat_g": 15.0, "carbs_g": 50.0},
    )

    # 3. 第一次替换：菌菇鸡肉焖饭 → 番茄鸡蛋面（命中菜谱 → 校准）
    resp = client.post(
        f"/api/v1/meals/{meal_id}/replace",
        headers=headers,
        json={"feedback": "太油腻了，想吃点清淡的"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meal"]["name"] == "番茄鸡蛋面"

    # 单餐营养：替换前未校准、替换后校准，且 delta = after - before
    meal_nut = body["meal_nutrition"]
    assert meal_nut["calibrated_before"] is False
    assert meal_nut["calibrated_after"] is True
    assert meal_nut["before"] and meal_nut["after"]
    for key in meal_nut["before"] | meal_nut["after"]:
        assert meal_nut["delta"][key] == round(
            meal_nut["after"].get(key, 0.0) - meal_nut["before"].get(key, 0.0), 1
        )

    # 全天营养对比：同一天换餐后数值变化
    day_nut = body["day_nutrition"]
    assert day_nut["day"] == "周一"
    assert day_nut["before"] and day_nut["after"]
    assert day_nut["delta"]

    # 购物清单联动：新食材被加入，旧食材（规划阶段生成、非本餐打标）保留
    sync = body["shopping_sync"]
    added_names = {item["name"] for item in sync["added"]}
    assert added_names == {"番茄", "鸡蛋", "面条"}
    shopping = client.get("/api/v1/shopping", headers=headers).json()
    shopping_names = {item["name"] for item in shopping}
    assert {"番茄", "鸡蛋", "面条", "鸡肉", "菌菇", "大米"} <= shopping_names

    # 4. 第二次替换：回退到菌菇鸡肉焖饭，验证本餐上轮生成的条目被移除
    resp2 = client.post(
        f"/api/v1/meals/{meal_id}/replace",
        headers=headers,
        json={"feedback": "还是不想吃面"},
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["meal"]["name"] == "菌菇鸡肉焖饭"
    removed_names = {item["name"] for item in body2["shopping_sync"]["removed"]}
    assert removed_names == {"番茄", "鸡蛋", "面条"}
    shopping2 = client.get("/api/v1/shopping", headers=headers).json()
    shopping_names2 = {item["name"] for item in shopping2}
    assert {"番茄", "鸡蛋", "面条"} & shopping_names2 == set()
    assert {"鸡肉", "菌菇", "大米"} <= shopping_names2

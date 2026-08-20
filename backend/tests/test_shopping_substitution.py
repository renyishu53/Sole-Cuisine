"""阶段 3 任务 B —— 食材替换确认闭环端点测试。

覆盖 ``POST /shopping/{id}/auto-substitute`` 与
``POST /shopping/{id}/substitution/accept`` 的确定性行为，
通过 monkeypatch 替换 ``get_substitution_service`` 单例，不依赖真实图谱/营养库。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.substitution import SubstitutionSuggestion


class _StubSubstitutionService:
    """可控替代服务 stub，按预设字典返回替代建议。"""

    def __init__(self, table: dict[str, list[SubstitutionSuggestion]]) -> None:
        self._table = table
        self.calls: list[tuple[str, int]] = []

    async def suggest(self, name: str, limit: int = 5) -> list[SubstitutionSuggestion]:
        self.calls.append((name, limit))
        return self._table.get(name.strip(), [])[:limit]


def _sub(name: str, reason: str = "测试替代") -> SubstitutionSuggestion:
    return SubstitutionSuggestion(name=name, reason=reason, similarity=0.9, source="graph")


def _create_item(
    client: TestClient, headers: dict[str, str], name: str = "牛肉"
) -> int:
    response = client.post(
        "/api/v1/shopping",
        headers=headers,
        json={"name": name, "category": "肉蛋奶", "quantity": "200g", "price": 30},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_auto_substitute_replaces_and_marks_item(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({"牛肉": [_sub("鸡胸肉")]})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")
    response = client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "鸡胸肉"
    assert body["substituted_from"] == "牛肉"
    assert body["substituted_accepted"] is None


def test_auto_substitute_without_suggestion_keeps_item_unchanged(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({})  # 无任何替代建议
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="神秘食材XYZ")
    response = client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "神秘食材XYZ"
    assert body["substituted_from"] is None
    assert body["substituted_accepted"] is None


def test_accept_substitution_accepts(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({"牛肉": [_sub("鸡胸肉")]})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")
    client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    response = client.post(
        f"/api/v1/shopping/{item_id}/substitution/accept",
        headers=auth_headers,
        json={"action": "accept"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "鸡胸肉"
    assert body["substituted_accepted"] is True


def test_accept_substitution_rejects_and_reverts(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({"牛肉": [_sub("鸡胸肉")]})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")
    client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    response = client.post(
        f"/api/v1/shopping/{item_id}/substitution/accept",
        headers=auth_headers,
        json={"action": "reject"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "牛肉"  # 回退到原食材
    assert body["substituted_from"] is None
    assert body["substituted_accepted"] is None


def test_accept_substitution_swap_with_explicit_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({"牛肉": [_sub("鸡胸肉"), _sub("豆腐")]})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")
    client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    response = client.post(
        f"/api/v1/shopping/{item_id}/substitution/accept",
        headers=auth_headers,
        json={"action": "swap", "name": "豆腐"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "豆腐"
    assert body["substituted_from"] == "牛肉"  # 始终保留最原始食材名
    assert body["substituted_accepted"] is None


def test_accept_substitution_swap_auto_picks_next(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({"牛肉": [_sub("鸡胸肉"), _sub("豆腐")]})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")
    client.post(f"/api/v1/shopping/{item_id}/auto-substitute", headers=auth_headers)

    response = client.post(
        f"/api/v1/shopping/{item_id}/substitution/accept",
        headers=auth_headers,
        json={"action": "swap"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "豆腐"  # 自动召回与当前名称不同的下一条
    assert body["substituted_from"] == "牛肉"


def test_accept_substitution_requires_existing_substitution(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    stub = _StubSubstitutionService({})
    monkeypatch.setattr("app.api.router.get_substitution_service", lambda: stub)

    item_id = _create_item(client, auth_headers, name="牛肉")  # 未自动替换
    response = client.post(
        f"/api/v1/shopping/{item_id}/substitution/accept",
        headers=auth_headers,
        json={"action": "accept"},
    )
    assert response.status_code == 409

"""G14 MySQL 集成测试（真实 MySQL，CI 门禁）。

本项目主库为 MySQL，但单测一直跑在 SQLite 内存库。本测试在 CI 中提供真实
MySQL 时执行，覆盖：
  1. alembic 迁移在 MySQL 上完整跑通（含 server_default / JSON / Boolean 等方言差异）；
  2. 核心 API 在真实 MySQL 会话下的端到端冒烟（注册 → 营养目标 → 餐食替换 → 购物清单）。

本地沙箱无 MySQL 镜像（离线）时，由 ``SOLOCHEF_TEST_MYSQL_URL`` 缺失自动跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings
from app.db import Base, get_db
from app.main import app
from app.services import domain as domain_svc

MYSQL_URL = os.environ.get("SOLOCHEF_TEST_MYSQL_URL")

pytestmark = pytest.mark.skipif(
    not MYSQL_URL, reason="需要设置 SOLOCHEF_TEST_MYSQL_URL 指向真实 MySQL 才能运行"
)


def _run_migrations(mysql_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", mysql_url)
    command.upgrade(cfg, "head")


@pytest.fixture()
def mysql_app():
    assert MYSQL_URL is not None
    engine = create_async_engine(MYSQL_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 迁移建表
    _run_migrations(MYSQL_URL)

    # 用真实 MySQL 会话覆盖依赖；替换走 demo，保证确定性
    domain_svc.domain_operations_service._settings = Settings(llm_provider="demo")

    async def _override() -> object:
        async with session_factory() as session:
            yield session

    token = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides[get_db] = token  # 还原（可能为 None）

    # 清理：丢弃所有表，便于重复运行
    import asyncio

    async def _drop() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_drop())


def _register(client: TestClient, phone: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "verification_code": "123456",
            "password": "solochef-mysql",
            "display_name": "MySQL 集成用户",
        },
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_mysql_migration_and_core_api_smoke(mysql_app: TestClient) -> None:
    headers = _register(mysql_app, "13800000888")

    # 营养目标：写入画像后计算（命中 MySQL 持久化路径）
    mysql_app.put(
        "/api/v1/profile",
        headers=headers,
        json={
            "gender": "male",
            "age": 30,
            "height_cm": 175,
            "weight_kg": 70,
            "activity_level": "moderate",
            "goal_type": "maintain",
        },
    )
    goal = mysql_app.post("/api/v1/profile/nutrition-goal", headers=headers)
    assert goal.status_code == 200, goal.text
    assert goal.json()["target_calories"] > 0

    # 餐食创建 + 营养报告（聚合 MySQL 上的餐食）
    meal = mysql_app.post(
        "/api/v1/meals",
        headers=headers,
        json={
            "day": "周一",
            "name": "番茄鸡蛋面",
            "ingredients": ["番茄", "鸡蛋", "面条"],
            "cost": 20,
        },
    )
    assert meal.status_code == 201, meal.text

    nutrition = mysql_app.get("/api/v1/meals/nutrition", headers=headers)
    assert nutrition.status_code == 200, nutrition.text
    assert nutrition.json()["actual"]

    # 餐食替换（G07 联动）：写入 MySQL 并联动购物清单
    replaced = mysql_app.post(
        f"/api/v1/meals/{meal.json()['id']}/replace",
        headers=headers,
        json={"feedback": "想换个口味"},
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["meal_nutrition"] is not None
    assert body["day_nutrition"] is not None

    shopping = mysql_app.get("/api/v1/shopping", headers=headers)
    assert shopping.status_code == 200, shopping.text

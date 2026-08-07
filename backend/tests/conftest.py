from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.llm import DemoPlanGenerator
from app.ai.workflow import SoloChefWorkflow
from app.db import Base, get_db
from app.main import app
from app.services.planning import planning_service
from app.services.sms import SMSCodeMismatchError, SMSService

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
TestSessionFactory = async_sessionmaker(test_engine, expire_on_commit=False)


class StubKnowledgeRetriever:
    async def retrieve_graph(self, *args: object, **kwargs: object) -> tuple[list[object], str]:
        return [], "stub"

    async def retrieve_vector(
        self, *args: object, **kwargs: object
    ) -> tuple[list[object], str, str]:
        return [], "stub", "disabled"


async def override_get_db() -> AsyncIterator[AsyncSession]:
    async with TestSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(scope="session", autouse=True)
async def database_schema() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    planning_service._workflow = SoloChefWorkflow(
        knowledge=StubKnowledgeRetriever(),  # type: ignore[arg-type]
        generator=DemoPlanGenerator(),
    )

    async def verify_test_code(self: SMSService, phone: str, code: str) -> bool:
        if code != "123456":
            raise SMSCodeMismatchError("验证码错误")
        return True

    app.dependency_overrides[get_db] = override_get_db
    with patch.object(SMSService, "verify_code", new=verify_test_code):
        yield
    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def auth_session(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000001",
            "verification_code": "123456",
            "password": "solochef-test",
            "display_name": "测试用户",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture(scope="session")
def auth_headers(auth_session: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_session['access_token']}"}

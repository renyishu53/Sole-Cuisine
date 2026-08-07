from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_router import router as auth_router
from app.api.router import router
from app.core.config import get_settings
from app.db import close_database
from app.services.checkpoints import checkpoint_runtime
from app.services.knowledge import get_knowledge_service
from app.services.runtime import runtime_state


async def _create_tables() -> None:
    """在应用启动时按需创建数据表（幂等，兼容 MySQL / SQLite）。

    生产环境可改为 ``alembic upgrade head``；此处兜底保证新部署开箱即用。
    """
    from app.db.base import Base
    from app.db.session import engine

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:  # noqa: BLE001 - 数据库暂不可达不应阻断应用启动
        pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _create_tables()
    try:
        yield
    finally:
        await checkpoint_runtime.close()
        await get_knowledge_service().close()
        await runtime_state.close()
        await close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)

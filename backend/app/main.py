import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth_router import router as auth_router
from app.api.router import router
from app.core.config import get_settings
from app.db import close_database
from app.services.checkpoints import checkpoint_runtime
from app.services.knowledge import get_knowledge_service
from app.services.runtime import runtime_state

# 本地静态资源（菜谱 SVG 占位图等）。目录不存在不挂载，避免启动报错。
_STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _create_tables() -> None:
    """在应用启动时按需创建数据表（幂等，兼容 MySQL / SQLite）。

    生产环境可改为 ``alembic upgrade head``；此处兜底保证新部署开箱即用。
    """
    import app.models  # noqa: F401
    from app.db.base import Base
    from app.db.session import engine

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:  # noqa: BLE001 - 数据库暂不可达不应阻断应用启动
        pass


async def _bootstrap_knowledge() -> None:
    """新部署开箱即用：启动时遍历 knowledge_docs/ 幂等入库（Milvus + Neo4j）。

    Milvus/Neo4j 暂不可达或已禁用时降级跳过，不阻断应用启动；可后续通过
    ``POST /api/v1/knowledge/bootstrap`` 手动重灌。
    """
    if not settings.rag_enabled or not settings.auto_bootstrap_knowledge:
        return
    try:
        await get_knowledge_service().bootstrap(1)
    except Exception:  # noqa: BLE001 - 检索底座未就绪不应阻断应用启动
        pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _create_tables()
    # Bootstrap may download/load embedding models and index many documents.
    # It is optional enrichment, so never hold the API in startup state for it.
    bootstrap_task = asyncio.create_task(_bootstrap_knowledge())
    try:
        yield
    finally:
        bootstrap_task.cancel()
        with suppress(asyncio.CancelledError):
            await bootstrap_task
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

# 挂载本地静态资源（菜谱 SVG 等），目录不存在则跳过
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# SQLite（含 aiosqlite）不支持连接池参数 pool_size/max_overflow/pool_timeout，
# 需按方言区分构造引擎，避免测试内存库或本地 SQLite 文件库启动失败。
_is_sqlite = settings.database_url.startswith("sqlite")
_engine_kwargs: dict[str, object] = {
    "pool_pre_ping": True,
    "pool_recycle": settings.db_pool_recycle,
}
if not _is_sqlite:
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    session = SessionFactory()
    try:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
    finally:
        await session.close()


async def verify_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def close_database() -> None:
    await engine.dispose()

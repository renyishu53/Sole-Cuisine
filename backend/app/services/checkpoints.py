"""LangGraph 检查点保存器运行时。

SoloChef 使用 PostgreSQL 作为业务数据库和 LangGraph 短期记忆保存器。
将 ``CHECKPOINT_BACKEND=postgres`` 后使用 ``AsyncPostgresSaver``，图状态可跨
worker/进程读取。Redis 继续用于队列、缓存和实时状态。
"""

import asyncio
import logging
import sys
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)


if sys.platform == "win32":
    # psycopg's async connection requires the selector loop on Windows;
    # ProactorEventLoop raises InterfaceError during AsyncPostgresSaver setup.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class CheckpointRuntime:
    """检查点保存器运行时，负责按配置创建并缓存 saver 实例。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._saver: BaseCheckpointSaver[str] | None = None
        self._resource: Any = None

    async def get(self) -> BaseCheckpointSaver[str]:
        """返回进程内缓存的检查点保存器。

        首次调用时按 ``CHECKPOINT_BACKEND`` 惰性创建，后续复用同一实例。
        ``postgres`` 使用 ``AsyncPostgresSaver``，工作流状态会写入业务
        PostgreSQL；仅在开发环境连接失败时才降级为 ``InMemorySaver``。
        """
        if self._saver is None:
            backend = self._settings.checkpoint_backend.lower()
            if backend == "postgres":
                try:
                    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                    postgres_url = (
                        self._settings.checkpoint_postgres_url
                        or self._settings.database_url.replace(
                            "+asyncpg", "", 1
                        )
                    )
                    resource = AsyncPostgresSaver.from_conn_string(postgres_url)
                    saver = await resource.__aenter__()
                    self._resource = resource
                    await saver.setup()
                    self._saver = saver
                except Exception as exc:
                    logger.warning(
                        "PostgreSQL checkpointer initialization failed; falling back "
                        "to InMemorySaver in non-production mode: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    if self._settings.app_env.lower() == "production":
                        raise RuntimeError(
                            "checkpoint_backend=postgres 但 PostgreSQL Checkpointer 不可用"
                        ) from exc
                    self._saver = InMemorySaver()
            elif backend == "redis":
                redis_url = self._settings.checkpoint_redis_url or self._settings.redis_url
                try:
                    from langgraph.checkpoint.redis import RedisSaver

                    ttl = {
                        "default_ttl": self._settings.checkpoint_ttl_seconds,
                        "refresh_on_read": True,
                    }
                    try:
                        resource = RedisSaver.from_conn_string(redis_url, ttl=ttl)
                    except TypeError:
                        # 早期扩展版本没有 TTL 参数；仍可使用 Redis 持久化。
                        resource = RedisSaver.from_conn_string(redis_url)
                    # langgraph-checkpoint-redis returns a context manager so that
                    # its connection pool is closed deterministically. Keep the
                    # resource alive for the application's whole process lifetime.
                    if hasattr(resource, "__enter__"):
                        saver = resource.__enter__()
                        self._resource = resource
                    else:
                        saver = resource
                    if hasattr(saver, "setup"):
                        await asyncio.to_thread(saver.setup)
                    self._saver = saver
                except Exception as exc:  # Redis 扩展、连接和初始化都应统一处理。
                    logger.warning(
                        "Redis checkpointer initialization failed; falling back to "
                        "InMemorySaver in non-production mode: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    if self._settings.app_env.lower() == "production":
                        raise RuntimeError(
                            "checkpoint_backend=redis 但 Redis Checkpointer 不可用"
                        ) from exc
                    self._saver = InMemorySaver()
            else:
                self._saver = InMemorySaver()
        return self._saver

    async def close(self) -> None:
        """释放检查点后端资源。

        外部 saver 持有的连接池在应用关闭时释放；内存 saver 无需额外处理。
        """
        if self._resource is not None:
            if hasattr(self._resource, "__aexit__"):
                await self._resource.__aexit__(None, None, None)
            elif hasattr(self._resource, "__exit__"):
                self._resource.__exit__(None, None, None)
        self._resource = None
        self._saver = None


checkpoint_runtime = CheckpointRuntime(get_settings())

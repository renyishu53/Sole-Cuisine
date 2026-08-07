import asyncio
import inspect
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.ai.llm import PlanDraft
from app.core.config import Settings, get_settings
from app.schemas import domain as domain_schemas


class AsyncThreadedPostgresSaver(BaseCheckpointSaver[str]):
    """Async adapter for the official saver that works with Windows Proactor loops."""

    def __init__(self, saver: PostgresSaver) -> None:
        super().__init__(serde=saver.serde)
        self._saver = saver

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self._saver.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        records = await asyncio.to_thread(
            lambda: list(self._saver.list(config, filter=filter, before=before, limit=limit))
        )
        for record in records:
            yield record

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self._saver.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._saver.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self._saver.delete_thread, thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self._saver.get_next_version(current, channel)


class CheckpointRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ConnectionPool | None = None
        self._saver: AsyncThreadedPostgresSaver | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> AsyncThreadedPostgresSaver | None:
        if not self._settings.database_url.startswith("postgresql"):
            return None
        if self._saver is not None:
            return self._saver
        async with self._lock:
            if self._saver is not None:
                return self._saver
            dsn = self._settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

            def setup() -> tuple[ConnectionPool, AsyncThreadedPostgresSaver]:
                pool = ConnectionPool(
                    conninfo=dsn,
                    open=True,
                    min_size=1,
                    max_size=5,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                    },
                )
                allowed_types = [
                    (value.__module__, value.__name__)
                    for value in vars(domain_schemas).values()
                    if inspect.isclass(value) and value.__module__ == domain_schemas.__name__
                ]
                allowed_types.append((PlanDraft.__module__, PlanDraft.__name__))
                serde = JsonPlusSerializer(allowed_msgpack_modules=allowed_types)
                saver = PostgresSaver(pool, serde=serde)  # type: ignore[arg-type]
                saver.setup()
                return pool, AsyncThreadedPostgresSaver(saver)

            self._pool, self._saver = await asyncio.to_thread(setup)
            return self._saver

    async def close(self) -> None:
        if self._pool is not None:
            await asyncio.to_thread(self._pool.close)
        self._pool = None
        self._saver = None


checkpoint_runtime = CheckpointRuntime(get_settings())

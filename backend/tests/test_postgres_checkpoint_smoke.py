"""Local integration coverage for the PostgreSQL LangGraph checkpointer."""

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from app.core.config import get_settings
from app.services.checkpoints import CheckpointRuntime


@pytest.mark.asyncio
async def test_postgres_checkpointer_is_active() -> None:
    settings = get_settings()
    if settings.checkpoint_backend.lower() != "postgres":
        pytest.skip("PostgreSQL checkpointer is not enabled")

    runtime = CheckpointRuntime(settings)
    try:
        saver = await runtime.get()
        assert type(saver).__name__ == "AsyncPostgresSaver"
        thread_id = "postgres-checkpoint-smoke"
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        stored_config = await saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": -1, "parents": {}},
            {},
        )
        restored = await saver.aget_tuple(stored_config)
        assert restored is not None
        assert restored.checkpoint["id"] == checkpoint["id"]
        await saver.adelete_thread(thread_id)
    finally:
        await runtime.close()

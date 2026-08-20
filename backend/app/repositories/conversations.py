"""对话与后台任务数据访问（去家庭化版，user 维度）。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BackgroundJob, ChatMessage, ChatSession


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, user_id: int, title: str) -> ChatSession:
        record = ChatSession(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            status="active",
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def list_sessions(self, user_id: int, query: str = "") -> list[ChatSession]:
        statement = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
            .limit(50)
        )
        if query:
            statement = statement.where(ChatSession.title.ilike(f"%{query}%"))
        return list((await self._session.scalars(statement)).all())

    async def get_session(self, session_id: str, user_id: int) -> ChatSession | None:
        return await self._session.scalar(
            select(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .options(selectinload(ChatSession.messages))
        )

    async def get_message(
        self, message_id: int, user_id: int
    ) -> ChatMessage | None:
        """按消息 ID 查询单条消息（带 user_id 隔离）。"""
        return await self._session.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id, ChatMessage.user_id == user_id
            )
        )

    async def add_message(
        self,
        chat: ChatSession,
        *,
        role: str,
        content: str,
        run_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=chat.id,
            user_id=chat.user_id,
            role=role,
            content=content,
            run_id=run_id,
            payload=payload or {},
        )
        chat.updated_at = datetime.now(UTC)
        if run_id is not None:
            chat.last_run_id = run_id
        self._session.add(message)
        await self._session.commit()
        await self._session.refresh(message)
        return message

    async def set_status(self, chat: ChatSession, status: str) -> None:
        chat.status = status
        await self._session.commit()

    async def rename_session(self, chat: ChatSession, title: str) -> None:
        chat.title = title
        chat.updated_at = datetime.now(UTC)
        await self._session.commit()

    async def delete_session(self, chat: ChatSession) -> None:
        await self._session.delete(chat)
        await self._session.commit()


class BackgroundJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        kind: str,
        payload: dict[str, object],
        idempotency_key: str | None = None,
        priority: str = "normal",
    ) -> BackgroundJob:
        job = BackgroundJob(
            id=str(uuid4()),
            user_id=user_id,
            kind=kind,
            status="queued",
            payload=payload,
            result={},
            idempotency_key=idempotency_key,
            priority=priority,
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def get(self, job_id: str, user_id: int) -> BackgroundJob | None:
        return await self._session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id,
                BackgroundJob.user_id == user_id,
            )
        )

    async def get_unscoped(self, job_id: str) -> BackgroundJob | None:
        return await self._session.get(BackgroundJob, job_id)

    async def mark_running(self, job: BackgroundJob) -> None:
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await self._session.commit()

    async def mark_completed(self, job: BackgroundJob, result: dict[str, object]) -> None:
        job.status = "completed"
        job.result = result
        job.finished_at = datetime.now(UTC)
        await self._session.commit()

    async def mark_failed(self, job: BackgroundJob, error_message: str) -> None:
        job.status = "failed"
        job.error_message = error_message[:2000]
        job.finished_at = datetime.now(UTC)
        await self._session.commit()

    async def cancel(self, job: BackgroundJob) -> None:
        """将任务标记为已取消（仅 queued/running 可取消）。"""
        if job.status in {"queued", "running"}:
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
            await self._session.commit()

    async def mark_dead_letter(self, job: BackgroundJob, reason: str) -> None:
        """任务重试耗尽后转入死信状态，保留错误原因供排查。"""
        job.status = "dead_letter"
        job.error_message = reason[:2000]
        job.finished_at = datetime.now(UTC)
        await self._session.commit()

    async def list_recent(self, user_id: int, limit: int = 20) -> list[BackgroundJob]:
        """返回用户最近的后台任务（用于监控面板）。"""
        statement = (
            select(BackgroundJob)
            .where(BackgroundJob.user_id == user_id)
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_dead_letter(self, user_id: int) -> list[BackgroundJob]:
        """返回用户的死信任务列表。"""
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.user_id == user_id,
                BackgroundJob.status == "dead_letter",
            )
            .order_by(BackgroundJob.finished_at.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def count_by_status(self, user_id: int) -> dict[str, int]:
        """按状态统计用户后台任务数量。"""
        statement = (
            select(BackgroundJob.status, func.count(BackgroundJob.id))
            .where(BackgroundJob.user_id == user_id)
            .group_by(BackgroundJob.status)
        )
        rows = (await self._session.execute(statement)).all()
        return {str(status): int(count) for status, count in rows}

    async def prune_terminal_before(self, cutoff: datetime) -> int:
        """删除指定时间之前已进入终态的任务记录，返回删除条数。"""
        terminal = ("completed", "failed", "cancelled", "dead_letter")
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.status.in_(terminal),
                BackgroundJob.finished_at.is_not(None),
                BackgroundJob.finished_at < cutoff,
            )
        )
        jobs = list((await self._session.scalars(statement)).all())
        for job in jobs:
            await self._session.delete(job)
        if jobs:
            await self._session.commit()
        return len(jobs)

import asyncio
from base64 import b64decode
from datetime import UTC, datetime, timedelta

from celery import Celery, Task  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "solochef",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# ── 队列隔离：knowledge（文档入库）/ graph（图谱同步）/ maintenance（清理）──
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="Asia/Shanghai",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "knowledge": {"exchange": "knowledge", "routing_key": "knowledge"},
        "graph": {"exchange": "graph", "routing_key": "graph"},
        "maintenance": {"exchange": "maintenance", "routing_key": "maintenance"},
    },
    task_routes={
        "solochef.process_knowledge_text": {"queue": "knowledge"},
        "solochef.process_knowledge_file": {"queue": "knowledge"},
        "solochef.sync_member_graph": {"queue": "graph"},
        "solochef.cleanup_old_jobs": {"queue": "maintenance"},
    },
    # 结果清理策略：Celery backend 结果 1 小时后过期，避免 Redis 膨胀
    result_expires=3600,
    # 定时任务：每天凌晨 3 点清理 30 天前的终态任务
    beat_schedule={
        "cleanup-old-jobs": {
            "task": "solochef.cleanup_old_jobs",
            "schedule": crontab(hour=3, minute=0),
            "args": (30,),
        },
    },
)


def _mark_dead_letter(job_id: str, reason: str) -> None:
    """任务重试耗尽后转入死信存储（同步封装，供 Celery 回调调用）。"""
    asyncio.run(_mark_dead_letter_async(job_id, reason))


async def _mark_dead_letter_async(job_id: str, reason: str) -> None:
    from app.db.session import SessionFactory
    from app.repositories import BackgroundJobRepository

    async with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        job = await repository.get_unscoped(job_id)
        if job is not None and job.status not in {"completed", "cancelled"}:
            await repository.mark_dead_letter(
                job, f"重试耗尽：{reason[:1800]}"
            )


def cancel_running_task(job_id: str) -> bool:
    """撤销 Celery 中正在运行的任务，返回是否发出 revoke 指令。"""
    try:
        celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")
        return True
    except Exception:  # noqa: BLE001 - broker 不可用时降级为仅标记 DB
        return False


class DeadLetterTask(Task):  # type: ignore[misc]
    """任务重试耗尽后自动转入死信存储的基类。"""

    def on_failure(  # type: ignore[override]
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: object,
    ) -> None:
        if self.request.retries >= getattr(self, "max_retries", 3):
            job_id = str(args[0]) if args else task_id
            _mark_dead_letter(job_id, f"{type(exc).__name__}: {exc}")
        super().on_failure(exc, task_id, args, kwargs, einfo)


@celery_app.task(
    name="solochef.process_knowledge_text",
    base=DeadLetterTask,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def process_knowledge_text(job_id: str) -> None:
    asyncio.run(_process_knowledge_text(job_id))


async def _process_knowledge_text(job_id: str) -> None:
    from app.db.session import SessionFactory
    from app.repositories import BackgroundJobRepository
    from app.services.knowledge import KnowledgeService

    async with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        job = await repository.get_unscoped(job_id)
        if job is None:
            return
        await repository.mark_running(job)
        knowledge = KnowledgeService(get_settings())
        try:
            document = await knowledge.ingest_text(
                name=str(job.payload["name"]),
                category=str(job.payload["category"]),
                content=str(job.payload["content"]),
                user_id=job.user_id,
            )
            await repository.mark_completed(job, document.model_dump(mode="json"))
        except Exception as exc:
            await repository.mark_failed(job, f"{type(exc).__name__}: {str(exc)}")
            raise
        finally:
            await knowledge.close()


@celery_app.task(
    name="solochef.process_knowledge_file",
    base=DeadLetterTask,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def process_knowledge_file(job_id: str) -> None:
    asyncio.run(_process_knowledge_file(job_id))


async def _process_knowledge_file(job_id: str) -> None:
    from app.db.session import SessionFactory
    from app.repositories import BackgroundJobRepository
    from app.services.knowledge import KnowledgeService

    async with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        job = await repository.get_unscoped(job_id)
        if job is None:
            return
        await repository.mark_running(job)
        knowledge = KnowledgeService(get_settings())
        try:
            document = await knowledge.ingest_file(
                name=str(job.payload["name"]),
                category=str(job.payload["category"]),
                payload=b64decode(str(job.payload["content_base64"]), validate=True),
                user_id=job.user_id,
            )
            await repository.mark_completed(job, document.model_dump(mode="json"))
        except Exception as exc:
            await repository.mark_failed(job, f"{type(exc).__name__}: {str(exc)}")
            raise
        finally:
            await knowledge.close()


@celery_app.task(
    name="solochef.sync_member_graph",
    base=DeadLetterTask,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_member_graph(job_id: str) -> None:
    asyncio.run(_sync_member_graph(job_id))


async def _sync_member_graph(job_id: str) -> None:
    from app.db.session import SessionFactory
    from app.repositories import (
        BackgroundJobRepository,
        DomainRepository,
        PlanningRepository,
    )
    from app.services.knowledge import KnowledgeService

    async with SessionFactory() as session:
        jobs = BackgroundJobRepository(session)
        job = await jobs.get_unscoped(job_id)
        if job is None:
            return
        await jobs.mark_running(job)
        knowledge = KnowledgeService(get_settings())
        try:
            # Phase 3 清理：calendar_events / plan_tasks / plan_budgets 表已删除，
            # 图谱同步仅含用户画像（忌口/偏好）与领域数据（菜谱/计划），
            # 预算改由 WeeklyPlan.budget 标量列派生（已并入 plans 节点，不再单独建 Budget 节点）。
            domain_repository = DomainRepository(session)
            planning = PlanningRepository(session)
            recipes = await domain_repository.list_recipes(job.user_id)
            plans = await planning.list_plans(job.user_id)
            domain: dict[str, list[dict[str, object]]] = {
                "recipes": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "tags": item.tags,
                        "allergens": item.allergens,
                        "ingredients": item.ingredients,
                    }
                    for item in recipes
                ],
                "plans": [
                    {
                        "id": item.id,
                        "version": item.version,
                        "summary": item.summary,
                        "is_active": item.is_active,
                    }
                    for item in plans
                ],
            }
            await knowledge.graph_store.sync_user_context(job.user_id, None, domain)
            await jobs.mark_completed(
                job,
                {
                    "recipes": len(recipes),
                    "plans": len(plans),
                },
            )
        except Exception as exc:
            await jobs.mark_failed(job, f"{type(exc).__name__}: {str(exc)}")
            raise
        finally:
            await knowledge.close()


@celery_app.task(name="solochef.cleanup_old_jobs")
def cleanup_old_jobs(days_old: int = 30) -> int:
    """清理指定天数前已进入终态的后台任务记录（含死信）。"""
    return asyncio.run(_cleanup_old_jobs(days_old))


async def _cleanup_old_jobs(days_old: int) -> int:
    from app.db.session import SessionFactory
    from app.repositories import BackgroundJobRepository

    cutoff = datetime.now(UTC) - timedelta(days=days_old)
    async with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        removed = await repository.prune_terminal_before(cutoff)
    return removed

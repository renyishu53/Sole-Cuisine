import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import TokenSink, token_sink
from app.ai.workflow import SoloChefWorkflow
from app.models import NutritionGoal, UserProfile
from app.repositories.feedback import FeedbackRepository
from app.repositories.planning import PlanningRepository
from app.schemas import (
    AgentRun,
    AgentStatus,
    AgentStep,
    PlanningRequest,
    PlanningResponse,
)
from app.services.checkpoints import checkpoint_runtime
from app.services.nutrition import nutrition_goal_to_targets


class PlanningService:
    def __init__(self, workflow: SoloChefWorkflow | None = None) -> None:
        self._workflow = workflow or SoloChefWorkflow()

    async def _configure_checkpointer(self, session: AsyncSession | None) -> bool:
        """配置工作流检查点保存器。

        SoloChef 使用进程内 ``InMemorySaver``，不依赖数据库方言。
        仅在存在数据库会话时启用——无会话场景（如单元测试 stub）跳过检查点。
        """
        if session is None:
            return False
        checkpointer = await checkpoint_runtime.get()
        self._workflow.set_checkpointer(checkpointer)
        return checkpointer is not None

    @staticmethod
    async def _load_taste_profile(
        session: AsyncSession | None, user_id: int
    ) -> dict[str, object]:
        """读取历史执行反馈聚合出的口味画像，注入本轮规划。

        这是"反馈 → 记忆 → 下一轮规划"闭环的入口：没有 Session 或表尚未迁移时
        返回空画像，餐食智能体退回成员静态偏好，不影响主链路。
        """
        if session is None:
            return {}
        try:
            profile = await FeedbackRepository(session).taste_profile(user_id)
        except Exception:  # noqa: BLE001 - 画像缺失不应阻断规划
            return {}
        return {} if profile.is_empty else profile.as_prompt_payload()

    @staticmethod
    async def _load_nutrition_targets(
        session: AsyncSession | None, user_id: int
    ) -> dict[str, float]:
        """读取用户营养目标，注入工作流 Verifier 做达成率校验。

        无 Session 或未设置目标时返回空字典，Verifier 跳过营养校验。
        """
        if session is None:
            return {}
        try:
            goal = await session.scalar(
                select(NutritionGoal).where(NutritionGoal.user_id == user_id)
            )
        except Exception:  # noqa: BLE001 - 目标缺失不应阻断规划
            return {}
        return nutrition_goal_to_targets(goal) if goal is not None else {}

    @staticmethod
    async def _load_user_profile_constraints(
        session: AsyncSession | None, user_id: int
    ) -> tuple[list[str], list[str]]:
        """读取单人用户画像的忌口/过敏约束与饮食偏好，注入工作流。

        SoloChef 去家庭化后，忌口校验的唯一数据源是 ``UserProfile.constraints``
        与 ``preferences``。无 Session 或未建档时返回空列表，餐食智能体与
        Verifier 退回家庭时期遗留的 ``members`` 兼容回退，不影响主链路。
        """
        if session is None:
            return [], []
        try:
            profile = await session.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        except Exception:  # noqa: BLE001 - 画像缺失不应阻断规划
            return [], []
        if profile is None:
            return [], []
        return list(profile.constraints), list(profile.preferences)

    @staticmethod
    async def _load_goal_type(session: AsyncSession | None, user_id: int) -> str | None:
        """读取用户营养目标取向（bulk/cut/maintain），注入向量检索做目标型文档过滤。

        无 Session 或未建档时返回 ``None``，检索退化为不过滤（等价通用召回）。
        """
        if session is None:
            return None
        try:
            profile = await session.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        except Exception:  # noqa: BLE001 - 画像缺失不应阻断规划
            return None
        return profile.goal_type if profile is not None else None

    async def _load_lifestyle_constraints(
        self, session: AsyncSession | None, user_id: int
    ) -> tuple[int | None, list[str]]:
        """加载生活约束（备餐时间上限与可用厨具），供 meal_agent 精准读取。

        画像缺失或读取失败时回退到空约束，不阻断规划。
        """
        if session is None:
            return None, []
        try:
            profile = await session.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        except Exception:  # noqa: BLE001 - 画像缺失不应阻断规划
            return None, []
        if profile is None:
            return None, []
        return profile.prep_time_max, list(profile.kitchenware)

    async def generate(
        self,
        request: PlanningRequest,
        session: AsyncSession | None = None,
        on_step: Callable[[AgentStep], Awaitable[None]] | None = None,
        on_token: TokenSink | None = None,
    ) -> PlanningResponse:
        await self._configure_checkpointer(session)
        taste_profile = await self._load_taste_profile(session, request.user_id)
        nutrition_targets = await self._load_nutrition_targets(session, request.user_id)
        user_constraints, user_preferences = await self._load_user_profile_constraints(
            session, request.user_id
        )
        prep_time_max, kitchenware = await self._load_lifestyle_constraints(
            session, request.user_id
        )
        goal_type = await self._load_goal_type(session, request.user_id)
        run_id = uuid4()
        started = perf_counter()
        repo = PlanningRepository(session) if session is not None else None
        if repo is not None:
            await repo.create_agent_run(
                str(run_id),
                user_id=request.user_id,
                prompt=request.prompt,
                status="running",
            )
        token = token_sink.set(on_token)
        try:

            async def persist_step(step: AgentStep) -> None:
                if repo is not None:
                    await repo.update_agent_run(
                        str(run_id),
                        request.user_id,
                        checkpoint={
                            "last_completed_node": step.name,
                            "step": step.model_dump(mode="json"),
                            "resumable": True,
                            "updated_at": datetime.now(UTC).isoformat(),
                        },
                    )
                if on_step is not None:
                    await on_step(step)

            response = await self._workflow.run(
                request,
                run_id=run_id,
                on_step=persist_step,
                taste_profile=taste_profile,
                nutrition_targets=nutrition_targets,
                user_constraints=user_constraints,
                user_preferences=user_preferences,
                prep_time_max=prep_time_max,
                kitchenware=kitchenware,
                goal_type=goal_type,
            )
        except asyncio.CancelledError:
            if repo is not None:
                await session.rollback()  # type: ignore[union-attr]
                await repo.update_agent_run(
                    str(run_id),
                    request.user_id,
                    status="failed",
                    duration_ms=max(1, round((perf_counter() - started) * 1000)),
                    error_message="用户取消了规划",
                    error_type="CancelledError",
                    failed_step="planner",
                    finished_at=datetime.now(UTC),
                )
            raise
        except Exception as exc:
            if repo is not None:
                assert session is not None
                await session.rollback()
                await repo.update_agent_run(
                    str(run_id),
                    request.user_id,
                    status="failed",
                    duration_ms=max(1, round((perf_counter() - started) * 1000)),
                    error_message=str(exc)[:2000] or "工作流执行失败",
                    error_type=type(exc).__name__,
                    failed_step=(
                        "planner" if type(exc).__name__ == "LLMGenerationError" else "workflow"
                    ),
                    finished_at=datetime.now(UTC),
                )
            raise
        finally:
            token_sink.reset(token)

        if repo is not None:
            await repo.update_agent_run(
                str(run_id),
                request.user_id,
                status="completed",
                duration_ms=sum(step.duration_ms for step in response.trace),
                steps=[step.model_dump(mode="json") for step in response.trace],
                sources=response.sources,
                summary=response.summary,
                llm_mode=getattr(self._workflow._generator, "mode", "demo"),
                payload=response.model_dump(mode="json"),
                checkpoint={
                    "last_completed_node": "final_planner",
                    "resumable": False,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                finished_at=datetime.now(UTC),
            )

        return response

    async def resume(
        self,
        run_id: UUID,
        *,
        user_id: int,
        session: AsyncSession,
        on_step: Callable[[AgentStep], Awaitable[None]] | None = None,
    ) -> PlanningResponse | None:
        repo = PlanningRepository(session)
        record = await repo.get_agent_run(str(run_id), user_id)
        if record is None:
            return None
        if record.status != "failed":
            raise ValueError("只有失败的 Agent Run 可以继续执行")
        if not await self._configure_checkpointer(session):
            return None

        started = perf_counter()
        previous_checkpoint = dict(record.checkpoint or {})
        await repo.update_agent_run(
            str(run_id),
            user_id,
            status="running",
            finished_at=None,
            error_message="",
            error_type="",
            failed_step="",
            checkpoint={
                **previous_checkpoint,
                "resume_requested_at": datetime.now(UTC).isoformat(),
                "resumable": True,
            },
        )

        async def persist_step(step: AgentStep) -> None:
            await repo.update_agent_run(
                str(run_id),
                user_id,
                checkpoint={
                    **previous_checkpoint,
                    "last_completed_node": step.name,
                    "step": step.model_dump(mode="json"),
                    "resumed": True,
                    "resumable": True,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            if on_step is not None:
                await on_step(step)

        try:
            response = await self._workflow.run(
                None,
                run_id=run_id,
                on_step=persist_step,
                resume=True,
            )
        except Exception as exc:
            await session.rollback()
            await repo.update_agent_run(
                str(run_id),
                user_id,
                status="failed",
                duration_ms=max(1, round((perf_counter() - started) * 1000)),
                error_message=str(exc)[:2000] or "工作流恢复失败",
                error_type=type(exc).__name__,
                failed_step="workflow",
                finished_at=datetime.now(UTC),
                checkpoint={
                    **previous_checkpoint,
                    "resume_failed_at": datetime.now(UTC).isoformat(),
                    "resumable": True,
                },
            )
            raise

        await repo.update_agent_run(
            str(run_id),
            user_id,
            status="completed",
            duration_ms=sum(step.duration_ms for step in response.trace),
            steps=[step.model_dump(mode="json") for step in response.trace],
            sources=response.sources,
            summary=response.summary,
            llm_mode=getattr(self._workflow._generator, "mode", "demo"),
            payload=response.model_dump(mode="json"),
            finished_at=datetime.now(UTC),
            checkpoint={
                **previous_checkpoint,
                "last_completed_node": "final_planner",
                "resumed": True,
                "resumable": False,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        return response

    async def get_run(self, run_id: UUID, user_id: int, session: AsyncSession) -> AgentRun | None:
        repo = PlanningRepository(session)
        record = await repo.get_agent_run(str(run_id), user_id)
        if record is None:
            return None
        return AgentRun(
            id=UUID(record.id),
            request=record.prompt,
            status=AgentStatus(record.status),
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=record.duration_ms,
            steps=[AgentStep(**step) for step in (record.steps or [])],
            error_message=record.error_message,
            error_type=record.error_type,
            failed_step=record.failed_step,
            checkpoint=record.checkpoint or {},
        )


planning_service = PlanningService()

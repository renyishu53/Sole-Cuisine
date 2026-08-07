"""Repository for plan persistence, agent run CRUD, and shopping/task item updates.

去家庭化版：所有查询以 user_id 隔离。
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.identity import (
    AgentRunRecord,
    PlanBudget,
    PlanMealItem,
    PlanShoppingItem,
    PlanTask,
    WeeklyPlan,
)
from app.schemas import TaskItem


class PlanningRepository:
    """Handles persistence of agent runs, weekly plans, and their sub-items."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _task_values(item: dict[str, Any]) -> dict[str, Any]:
        """Validate and strip non-persistent fields from task input dicts."""
        return TaskItem.model_validate(item).model_dump(
            exclude={"id", "assignee_member_id"}
        )

    # ── Agent Run ──────────────────────────────────────────────────────

    async def create_agent_run(
        self,
        run_id: str,
        *,
        user_id: int,
        prompt: str,
        status: str = "running",
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            id=run_id,
            user_id=user_id,
            status=status,
            prompt=prompt,
            payload={},
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def update_agent_run(
        self, run_id: str, user_id: int, **values: Any
    ) -> AgentRunRecord | None:
        record = await self._session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.id == run_id,
                AgentRunRecord.user_id == user_id,
            )
        )
        if record is None:
            return None
        for key, value in values.items():
            if hasattr(record, key):
                setattr(record, key, value)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def get_agent_run(self, run_id: str, user_id: int) -> AgentRunRecord | None:
        return await self._session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.id == run_id,
                AgentRunRecord.user_id == user_id,
            )
        )

    async def list_agent_runs(self, user_id: int, limit: int = 20) -> list[AgentRunRecord]:
        statement = (
            select(AgentRunRecord)
            .where(AgentRunRecord.user_id == user_id)
            .order_by(AgentRunRecord.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    # ── Plan CRUD ──────────────────────────────────────────────────────

    async def create_plan(self, user_id: int, **values: Any) -> WeeklyPlan:
        plan = WeeklyPlan(user_id=user_id, **values)
        self._session.add(plan)
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def get_or_create_active_plan(self, user_id: int) -> WeeklyPlan:
        plan = await self.get_active_plan(user_id)
        if plan is not None:
            return plan
        latest = await self._latest_plan(user_id)
        plan = WeeklyPlan(
            user_id=user_id,
            status="confirmed",
            version=(latest.version + 1) if latest is not None else 1,
            parent_plan_id=latest.id if latest is not None else None,
            is_active=True,
            prompt="手工维护的计划",
            budget=500,
            summary="手工维护的餐食、购物、任务和预算",
            conflicts=[],
            suggestions=[],
        )
        self._session.add(plan)
        await self._session.flush()
        return plan

    async def create_confirmed_plan(
        self,
        *,
        user_id: int,
        run_id: str,
        prompt: str,
        plan_values: dict[str, Any],
        meals: list[dict[str, Any]],
        shopping: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        budget: dict[str, Any],
    ) -> WeeklyPlan:
        active = await self.get_active_plan(user_id)
        latest_version = await self._session.scalar(
            select(func.max(WeeklyPlan.version)).where(WeeklyPlan.user_id == user_id)
        )
        if active is not None:
            active.is_active = False
            await self._session.flush()
        plan = WeeklyPlan(
            user_id=user_id,
            run_id=run_id,
            prompt=prompt,
            status="confirmed",
            version=(latest_version or 0) + 1,
            parent_plan_id=active.id if active is not None else None,
            is_active=True,
            **plan_values,
        )
        plan.meals = [
            PlanMealItem(**{key: value for key, value in item.items() if key != "id"})
            for item in meals
        ]
        plan.shopping_items = [
            PlanShoppingItem(**{key: value for key, value in item.items() if key != "id"})
            for item in shopping
        ]
        plan.tasks = [PlanTask(**self._task_values(item)) for item in tasks]
        plan.budget_record = PlanBudget(
            **{key: value for key, value in budget.items() if key != "id"}
        )
        self._session.add(plan)
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def confirm_plan(
        self,
        plan: WeeklyPlan,
        *,
        meals: list[dict[str, Any]],
        shopping: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        budget: dict[str, Any],
    ) -> WeeklyPlan:
        plan.status = "confirmed"
        for meal_data in meals:
            self._session.add(
                PlanMealItem(plan_id=plan.id, **{k: v for k, v in meal_data.items() if k != "id"})
            )
        for shop_data in shopping:
            self._session.add(
                PlanShoppingItem(
                    plan_id=plan.id, **{k: v for k, v in shop_data.items() if k != "id"}
                )
            )
        for task_data in tasks:
            self._session.add(PlanTask(plan_id=plan.id, **self._task_values(task_data)))
        self._session.add(
            PlanBudget(plan_id=plan.id, **{k: v for k, v in budget.items() if k != "id"})
        )
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def derive_plan(
        self, source: WeeklyPlan, *, user_id: int, prompt: str | None = None
    ) -> WeeklyPlan:
        active = await self.get_active_plan(source.user_id)
        if active is not None:
            active.is_active = False
        latest_version = await self._session.scalar(
            select(func.max(WeeklyPlan.version)).where(WeeklyPlan.user_id == source.user_id)
        )
        plan = WeeklyPlan(
            user_id=user_id,
            run_id=None,
            status="confirmed",
            version=(latest_version or 0) + 1,
            parent_plan_id=source.id,
            is_active=True,
            prompt=prompt or f"从 v{source.version} 派生",
            budget=source.budget,
            summary=source.summary,
            conflicts=list(source.conflicts),
            suggestions=list(source.suggestions),
        )
        plan.meals = [
            PlanMealItem(
                day=item.day,
                name=item.name,
                duration=item.duration,
                cost=item.cost,
                tags=list(item.tags),
                reason=item.reason,
                ingredients=list(item.ingredients),
            )
            for item in source.meals
        ]
        plan.shopping_items = [
            PlanShoppingItem(
                name=item.name,
                category=item.category,
                quantity=item.quantity,
                price=item.price,
                source=item.source,
                purchased=item.purchased,
            )
            for item in source.shopping_items
        ]
        plan.tasks = [
            PlanTask(
                title=item.title,
                assignee=item.assignee,
                duration=item.duration,
                due=item.due,
                status=item.status,
                category=item.category,
                scheduled_start_at=item.scheduled_start_at,
                scheduled_end_at=item.scheduled_end_at,
                recurrence_type=item.recurrence_type,
                recurrence_interval=item.recurrence_interval,
            )
            for item in source.tasks
        ]
        if source.budget_record is not None:
            budget = source.budget_record
            plan.budget_record = PlanBudget(
                limit=budget.limit,
                estimated=budget.estimated,
                saved=budget.saved,
                usage_percent=budget.usage_percent,
                categories=dict(budget.categories),
            )
        self._session.add(plan)
        await self._session.commit()
        return await self.get_plan(plan.id, plan.user_id) or plan

    async def get_plan(self, plan_id: int, user_id: int) -> WeeklyPlan | None:
        return await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.id == plan_id, WeeklyPlan.user_id == user_id)
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
                selectinload(WeeklyPlan.tasks),
                selectinload(WeeklyPlan.budget_record),
            )
        )

    async def get_plan_by_run_id(self, run_id: str, user_id: int) -> WeeklyPlan | None:
        return await self._session.scalar(
            select(WeeklyPlan).where(
                WeeklyPlan.run_id == run_id,
                WeeklyPlan.user_id == user_id,
            )
        )

    async def list_plans(self, user_id: int, limit: int = 20) -> list[WeeklyPlan]:
        statement = (
            select(WeeklyPlan)
            .where(WeeklyPlan.user_id == user_id)
            .order_by(WeeklyPlan.version.desc())
            .limit(limit)
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
                selectinload(WeeklyPlan.tasks),
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def get_active_plan(self, user_id: int) -> WeeklyPlan | None:
        return await self._session.scalar(
            select(WeeklyPlan)
            .where(
                WeeklyPlan.user_id == user_id,
                WeeklyPlan.is_active.is_(True),
            )
            .limit(1)
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
                selectinload(WeeklyPlan.tasks),
                selectinload(WeeklyPlan.budget_record),
            )
        )

    async def list_plan_versions(self, plan_id: int, user_id: int) -> list[WeeklyPlan] | None:
        if await self.get_plan(plan_id, user_id) is None:
            return None
        return await self.list_plans(user_id, limit=100)

    async def activate_plan(self, plan_id: int, user_id: int) -> WeeklyPlan | None:
        target = await self.get_plan(plan_id, user_id)
        if target is None:
            return None
        current = await self.get_active_plan(user_id)
        if current is not None and current.id != target.id:
            current.is_active = False
            await self._session.flush()
        target.is_active = True
        target.status = "confirmed"
        await self._session.commit()
        return await self.get_plan(target.id, user_id)

    async def rollback_plan(self, plan_id: int, user_id: int) -> WeeklyPlan | None:
        plan = await self.get_plan(plan_id, user_id)
        if plan is None or plan.parent_plan_id is None:
            return None
        return await self.activate_plan(plan.parent_plan_id, user_id)

    async def archive_plan(self, plan_id: int, user_id: int) -> WeeklyPlan | None:
        """将计划标记为归档：status=archived 且取消激活，独立于版本回滚。"""
        plan = await self.get_plan(plan_id, user_id)
        if plan is None:
            return None
        plan.status = "archived"
        plan.is_active = False
        await self._session.commit()
        return await self.get_plan(plan.id, user_id)

    async def list_archived_plans(self, user_id: int) -> list[WeeklyPlan]:
        """返回已归档计划列表，按版本降序。"""
        statement = (
            select(WeeklyPlan)
            .where(WeeklyPlan.user_id == user_id, WeeklyPlan.status == "archived")
            .order_by(WeeklyPlan.version.desc())
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
                selectinload(WeeklyPlan.tasks),
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def _latest_plan(self, user_id: int) -> WeeklyPlan | None:
        return await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.user_id == user_id)
            .order_by(WeeklyPlan.version.desc())
            .limit(1)
        )

    # ── Shopping / Task item updates ───────────────────────────────────

    async def get_shopping_item(self, item_id: int, user_id: int) -> PlanShoppingItem | None:
        return await self._session.scalar(
            select(PlanShoppingItem)
            .join(WeeklyPlan)
            .where(
                PlanShoppingItem.id == item_id,
                WeeklyPlan.user_id == user_id,
            )
        )

    async def get_meal_item(self, item_id: int, user_id: int) -> PlanMealItem | None:
        return await self._session.scalar(
            select(PlanMealItem)
            .join(WeeklyPlan)
            .where(PlanMealItem.id == item_id, WeeklyPlan.user_id == user_id)
        )

    async def get_task(self, task_id: int, user_id: int) -> PlanTask | None:
        return await self._session.scalar(
            select(PlanTask)
            .join(WeeklyPlan)
            .where(
                PlanTask.id == task_id,
                WeeklyPlan.user_id == user_id,
            )
        )

    async def get_budget(self, user_id: int) -> PlanBudget | None:
        plan = await self.get_active_plan(user_id)
        return plan.budget_record if plan is not None else None

    async def create_meal(self, user_id: int, **values: Any) -> PlanMealItem:
        plan = await self.get_or_create_active_plan(user_id)
        item = PlanMealItem(plan_id=plan.id, **values)
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def create_shopping_item(self, user_id: int, **values: Any) -> PlanShoppingItem:
        plan = await self.get_or_create_active_plan(user_id)
        item = PlanShoppingItem(plan_id=plan.id, **values)
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def create_task(self, user_id: int, **values: Any) -> PlanTask:
        plan = await self.get_or_create_active_plan(user_id)
        item = PlanTask(plan_id=plan.id, **values)
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def update_budget(self, user_id: int, **values: Any) -> PlanBudget:
        plan = await self.get_or_create_active_plan(user_id)
        budget = plan.budget_record
        if budget is None:
            budget = PlanBudget(
                plan_id=plan.id,
                limit=500,
                estimated=0,
                saved=500,
                usage_percent=0,
                categories={},
            )
            self._session.add(budget)
        for key, value in values.items():
            setattr(budget, key, value)
        budget.saved = max(0, budget.limit - budget.estimated)
        budget.usage_percent = min(100, round(budget.estimated / budget.limit * 100))
        await self._session.commit()
        await self._session.refresh(budget)
        return budget

    async def save_item(self, item: PlanMealItem | PlanShoppingItem | PlanTask) -> None:
        await self._session.commit()
        await self._session.refresh(item)

    async def delete_item(self, item: PlanMealItem | PlanShoppingItem | PlanTask) -> None:
        await self._session.delete(item)
        await self._session.commit()

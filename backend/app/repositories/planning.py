"""Repository for plan persistence, agent run CRUD, and shopping item updates.

去家庭化版：所有查询以 user_id 隔离。
Phase 3 清理：移除 PlanTask / PlanBudget 持久化（表已删除），
tasks 和 budget 仍由 PlanDraft → PlanningResponse 随计划返回，不再落表。
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.identity import (
    AgentRunRecord,
    PlanMealItem,
    PlanShoppingItem,
    WeeklyPlan,
)

_PLAN_TIMEZONE = ZoneInfo("Asia/Shanghai")
_WEEK_DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MEAL_TYPES = ("早餐", "午餐", "晚餐")
_EXPECTED_MEAL_SLOTS = {(day, meal_type) for day in _WEEK_DAYS for meal_type in _MEAL_TYPES}


def current_week_start_utc(now: datetime | None = None) -> datetime:
    """Return Monday 00:00 in the product timezone, normalized to UTC."""
    local_now = (now or datetime.now(UTC)).astimezone(_PLAN_TIMEZONE)
    local_monday = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_monday.astimezone(UTC)


def is_current_weekly_plan(plan: WeeklyPlan) -> bool:
    """A current plan must belong to this natural week and cover all 21 meals."""
    if plan.created_at is None or plan.status != "confirmed":
        return False
    created_at = plan.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    slots = {(meal.day, meal.meal_type) for meal in plan.meals}
    return (
        created_at >= current_week_start_utc()
        and len(plan.meals) == 21
        and slots == _EXPECTED_MEAL_SLOTS
    )


class PlanningRepository:
    """Handles persistence of agent runs, weekly plans, and their sub-items."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
            summary="手工维护的餐食与购物",
            conflicts=[],
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
            conflict_details=list(source.conflict_details),
            auto_fixes=list(source.auto_fixes),
            needs_manual_review=source.needs_manual_review,
            manual_review_hint=source.manual_review_hint,
        )
        plan.meals = [
            PlanMealItem(
                day=item.day,
                meal_type=item.meal_type,
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
        self._session.add(plan)
        await self._session.commit()
        return await self.get_plan(plan.id, plan.user_id) or plan

    async def derive_plan_with_modifications(
        self,
        source: WeeklyPlan,
        *,
        user_id: int,
        prompt: str,
        meals: list[dict[str, Any]],
        shopping: list[dict[str, Any]],
        budget: float | None = None,
        summary: str | None = None,
        conflicts: list[str] | None = None,
    ) -> WeeklyPlan:
        """从源计划派生新版本并应用修改后的 meals/shopping。

        与 :meth:`derive_plan` 不同：本方法接收"修改后的子项列表"，
        直接落库为新版本——不跑工作流，纯持久化。

        用于 ``POST /plans/{plan_id}/revise/{revise_id}/confirm`` 流程：
        ``PlanReviseService`` 已经在内存中算好修改结果，确认时调用本方法落库。

        Args:
            source: 源计划（用于继承 ``parent_plan_id`` 关系）。
            user_id: 当前用户 ID（隔离校验）。
            prompt: 本次修改的自然语言描述，作为新版本的 ``prompt``。
            meals: 修改后的餐食列表（dict 字段对齐 ``PlanMealItem``）。
            shopping: 修改后的购物清单列表（dict 字段对齐 ``PlanShoppingItem``）。
            budget: 可选的新预算；缺省继承源计划预算。
            summary: 可选的新摘要；缺省继承源计划摘要。
            conflicts: 可选的冲突提示；缺省继承。

        Returns:
            新创建的 :class:`WeeklyPlan`，已 commit 且 ``is_active=True``。
        """
        active = await self.get_active_plan(source.user_id)
        if active is not None:
            active.is_active = False
        latest_version = await self._session.scalar(
            select(func.max(WeeklyPlan.version)).where(WeeklyPlan.user_id == user_id)
        )
        plan = WeeklyPlan(
            user_id=user_id,
            run_id=None,
            status="confirmed",
            version=(latest_version or 0) + 1,
            parent_plan_id=source.id,
            is_active=True,
            prompt=prompt[:1000] if prompt else f"从 v{source.version} 派生",
            budget=budget if budget is not None else source.budget,
            summary=summary if summary is not None else source.summary,
            conflicts=list(conflicts) if conflicts is not None else list(source.conflicts),
            conflict_details=list(source.conflict_details),
            auto_fixes=list(source.auto_fixes),
            needs_manual_review=source.needs_manual_review,
            manual_review_hint=source.manual_review_hint,
        )
        plan.meals = [
            PlanMealItem(**{key: value for key, value in item.items() if key != "id"})
            for item in meals
        ]
        plan.shopping_items = [
            PlanShoppingItem(**{key: value for key, value in item.items() if key != "id"})
            for item in shopping
        ]
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
            )
        )

    async def get_plan_for_period(
        self,
        user_id: int,
        *,
        start: datetime,
        end: datetime,
    ) -> WeeklyPlan | None:
        """Return the newest confirmed plan generated within a reporting period."""
        statement = (
            select(WeeklyPlan)
            .where(
                WeeklyPlan.user_id == user_id,
                WeeklyPlan.status == "confirmed",
                WeeklyPlan.created_at >= start,
                WeeklyPlan.created_at < end,
            )
            .order_by(WeeklyPlan.version.desc())
            .limit(1)
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
            )
        )
        return await self._session.scalar(statement)

    async def list_confirmed_plan_dates(self, user_id: int) -> list[datetime]:
        """Return creation timestamps for every confirmed plan owned by the user."""
        statement = (
            select(WeeklyPlan.created_at)
            .where(WeeklyPlan.user_id == user_id, WeeklyPlan.status == "confirmed")
            .order_by(WeeklyPlan.created_at.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def list_plan_versions(self, plan_id: int, user_id: int) -> list[WeeklyPlan] | None:
        target = await self.get_plan(plan_id, user_id)
        if target is None:
            return None
        created_at = target.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        local_created_at = created_at.astimezone(_PLAN_TIMEZONE)
        local_monday = (local_created_at - timedelta(days=local_created_at.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_start = local_monday.astimezone(UTC)
        week_end = week_start + timedelta(days=7)
        statement = (
            select(WeeklyPlan)
            .where(
                WeeklyPlan.user_id == user_id,
                WeeklyPlan.status == "confirmed",
                WeeklyPlan.created_at >= week_start,
                WeeklyPlan.created_at < week_end,
            )
            .order_by(WeeklyPlan.version.desc())
            .options(
                selectinload(WeeklyPlan.meals),
                selectinload(WeeklyPlan.shopping_items),
            )
        )
        return list((await self._session.scalars(statement)).all())

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

    async def has_expired_plan(self, user_id: int) -> bool:
        """Return whether the newest plan is outside the current natural week."""
        latest = await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.user_id == user_id, WeeklyPlan.status != "archived")
            .order_by(WeeklyPlan.version.desc())
            .limit(1)
            .options(selectinload(WeeklyPlan.meals))
        )
        return latest is not None and not is_current_weekly_plan(latest)

    async def expire_old_plans(self, user_id: int) -> int:
        """Deactivate plans from prior weeks and legacy plans without 21 complete meal slots."""
        active_plans = list(
            (
                await self._session.scalars(
                    select(WeeklyPlan)
                    .where(WeeklyPlan.user_id == user_id, WeeklyPlan.is_active.is_(True))
                    .options(selectinload(WeeklyPlan.meals))
                )
            ).all()
        )
        expired_count = 0
        for plan in active_plans:
            if not is_current_weekly_plan(plan):
                plan.is_active = False
                expired_count += 1
        if expired_count:
            await self._session.commit()
        return expired_count

    # ── Shopping / Meal item updates ───────────────────────────────────

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

    async def save_item(self, item: PlanMealItem | PlanShoppingItem) -> None:
        await self._session.commit()
        await self._session.refresh(item)

    async def delete_item(self, item: PlanMealItem | PlanShoppingItem) -> None:
        await self._session.delete(item)
        await self._session.commit()

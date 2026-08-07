import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExpenseRecord,
    InventoryItem,
    PlanMealItem,
    PlanShoppingItem,
    PlanTask,
    RecipeRecord,
    TaskCompletion,
)
from app.repositories.planning import PlanningRepository
from app.services.unit_conversion import add_quantities, describe_conversion


class DomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recipes(self, user_id: int) -> list[RecipeRecord]:
        statement = (
            select(RecipeRecord)
            .where(RecipeRecord.user_id == user_id)
            .order_by(RecipeRecord.is_favorite.desc(), RecipeRecord.updated_at.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def get_recipe(self, recipe_id: int, user_id: int) -> RecipeRecord | None:
        return await self._session.scalar(
            select(RecipeRecord).where(
                RecipeRecord.id == recipe_id,
                RecipeRecord.user_id == user_id,
            )
        )

    async def create_recipe(
        self, user_id: int, **values: object
    ) -> RecipeRecord:
        recipe = RecipeRecord(
            user_id=user_id,
            created_by_user_id=user_id,
            **values,
        )
        self._session.add(recipe)
        await self._session.commit()
        await self._session.refresh(recipe)
        return recipe

    async def save_recipe(self, recipe: RecipeRecord, **values: object) -> RecipeRecord:
        for key, value in values.items():
            setattr(recipe, key, value)
        await self._session.commit()
        await self._session.refresh(recipe)
        return recipe

    async def delete_recipe(self, recipe: RecipeRecord) -> None:
        await self._session.delete(recipe)
        await self._session.commit()

    async def merge_shopping(
        self, user_id: int
    ) -> tuple[int, int, list[PlanShoppingItem], list[dict[str, Any]]]:
        """合并活跃计划中的重复购物项。

        使用 :mod:`app.services.unit_conversion` 进行单位归一化，支持
        "2 斤 + 500 克" 这类跨单位求和。返回 (合并组数, 移除条数, 合并后列表, 换算说明列表)。
        """
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        if plan is None:
            return 0, 0, [], []
        groups: dict[tuple[str, str], list[PlanShoppingItem]] = defaultdict(list)
        for item in plan.shopping_items:
            normalized = "".join(item.name.lower().split())
            aliases = {"西红柿": "番茄", "土豆": "马铃薯", "鸡蛋": "蛋", "生抽酱油": "生抽"}
            normalized = aliases.get(normalized, normalized)
            groups[(normalized, item.category)].append(item)
        merged_groups = 0
        removed = 0
        conversion_notes: list[dict[str, Any]] = []
        for items in groups.values():
            if len(items) < 2:
                continue
            merged_groups += 1
            primary, *duplicates = items
            # 优先用单位换算合并；失败则保留原算法（同单位求和或拼接）
            quantities = [item.quantity for item in items]
            merged_quantity = add_quantities(quantities)
            if merged_quantity is not None:
                primary.quantity = merged_quantity
                # 记录换算说明供前端展示
                for item in items:
                    note = describe_conversion(item.quantity)
                    if note:
                        conversion_notes.append({
                            "name": primary.name,
                            "original": item.quantity,
                            "converted": note,
                        })
            else:
                parsed = [
                    re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([^\d\s]+)\s*", item.quantity)
                    for item in items
                ]
                if all(parsed) and len({match.group(2) for match in parsed if match}) == 1:
                    unit = next(match.group(2) for match in parsed if match)
                    total = sum(float(match.group(1)) for match in parsed if match)
                    primary.quantity = f"{total:g} {unit}"
                else:
                    primary.quantity = " + ".join(dict.fromkeys(item.quantity for item in items))
            sources = list(dict.fromkeys(item.source for item in items if item.source))
            primary.price = round(sum(item.price for item in items), 2)
            primary.source = " / ".join(sources)
            primary.purchased = all(item.purchased for item in items)
            for duplicate in duplicates:
                await self._session.delete(duplicate)
                removed += 1
        await self._session.commit()
        refreshed = await PlanningRepository(self._session).get_active_plan(user_id)
        return (
            merged_groups,
            removed,
            list(refreshed.shopping_items) if refreshed else [],
            conversion_notes,
        )

    async def create_expense(
        self,
        user_id: int,
        **values: object,
    ) -> ExpenseRecord:
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        shopping_item_id = values.get("shopping_item_id")
        if shopping_item_id is not None:
            if not isinstance(shopping_item_id, int):
                raise ValueError("shopping_item_id must be an integer")
            item = await PlanningRepository(self._session).get_shopping_item(
                shopping_item_id, user_id
            )
            if item is None:
                raise ValueError("购物项不存在或不属于当前用户")
        expense = ExpenseRecord(
            user_id=user_id,
            plan_id=plan.id if plan else None,
            created_by_user_id=user_id,
            **values,
        )
        self._session.add(expense)
        await self._session.commit()
        await self._session.refresh(expense)
        return expense

    async def list_expenses(self, user_id: int) -> list[ExpenseRecord]:
        statement = (
            select(ExpenseRecord)
            .where(ExpenseRecord.user_id == user_id)
            .order_by(ExpenseRecord.occurred_at.desc(), ExpenseRecord.id.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def list_expenses_filtered(
        self,
        user_id: int,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        category: str | None = None,
    ) -> list[ExpenseRecord]:
        """按日期区间和分类筛选支出历史。"""
        statement = select(ExpenseRecord).where(ExpenseRecord.user_id == user_id)
        if start_date is not None:
            statement = statement.where(ExpenseRecord.occurred_at >= start_date)
        if end_date is not None:
            statement = statement.where(ExpenseRecord.occurred_at <= end_date)
        if category is not None and category != "全部":
            statement = statement.where(ExpenseRecord.category == category)
        statement = statement.order_by(
            ExpenseRecord.occurred_at.desc(), ExpenseRecord.id.desc()
        )
        return list((await self._session.scalars(statement)).all())

    async def list_expense_categories(self, user_id: int) -> list[str]:
        """返回当前用户已使用过的支出分类，供前端筛选下拉。"""
        statement = (
            select(ExpenseRecord.category)
            .where(ExpenseRecord.user_id == user_id)
            .distinct()
            .order_by(ExpenseRecord.category)
        )
        return list((await self._session.scalars(statement)).all())

    async def delete_expense(self, expense_id: int, user_id: int) -> bool:
        expense = await self._session.scalar(
            select(ExpenseRecord).where(
                ExpenseRecord.id == expense_id,
                ExpenseRecord.user_id == user_id,
            )
        )
        if expense is None:
            return False
        await self._session.delete(expense)
        await self._session.commit()
        return True

    async def complete_task(
        self,
        task: PlanTask,
        *,
        user_id: int,
        actual_duration: int,
        notes: str,
    ) -> TaskCompletion:
        task.status = "done"
        completion = TaskCompletion(
            user_id=user_id,
            task_id=task.id,
            completed_by_user_id=user_id,
            completed_at=datetime.now(UTC),
            actual_duration=actual_duration,
            notes=notes,
        )
        self._session.add(completion)
        await self._session.commit()
        await self._session.refresh(completion)
        return completion

    async def get_active_tasks(self, user_id: int) -> list[PlanTask]:
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        return list(plan.tasks) if plan is not None else []

    async def save_tasks(self) -> None:
        await self._session.commit()

    async def completion_workload(self, user_id: int) -> dict[int, int]:
        completions = list(
            (
                await self._session.scalars(
                    select(TaskCompletion).where(TaskCompletion.user_id == user_id)
                )
            ).all()
        )
        result: dict[int, int] = defaultdict(int)
        for completion in completions:
            result[completion.completed_by_user_id] += completion.actual_duration
        return result

    async def replace_meal(self, meal: PlanMealItem, **values: object) -> PlanMealItem:
        for key, value in values.items():
            setattr(meal, key, value)
        await self._session.commit()
        await self._session.refresh(meal)
        return meal

    async def expand_recurring_tasks(
        self, user_id: int, *, days: int = 30
    ) -> list[dict[str, Any]]:
        """展开周期任务为未来 ``days`` 天的具体发生项。

        遍历活跃计划中所有 ``recurrence_type != 'none'`` 的任务，按其
        ``scheduled_start_at`` 与 ``recurrence_interval`` 推算未来发生时间。
        返回列表元素包含 task_id、title、assignee、occurrence_at 等字段，
        供前端"未来任务预览"展示，不会写入数据库。
        """
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        if plan is None:
            return []
        now = datetime.now(UTC)
        horizon = now + timedelta(days=days)
        expansions: list[dict[str, Any]] = []
        for task in plan.tasks:
            if task.recurrence_type is None or task.recurrence_type == "none":
                continue
            anchor = task.scheduled_start_at or task.created_at
            if anchor is None:
                continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            interval = max(int(task.recurrence_interval or 1), 1)
            step = _recurrence_step(task.recurrence_type, interval)
            if step is None:
                continue
            # 从 anchor 开始向前推进到 >= now，再继续到 horizon
            current = anchor
            # 若 anchor 已过去，跳到第一个 >= now 的发生点
            while current < now:
                current = current + step
            counter = 0
            while current <= horizon and counter < 50:  # 安全上限
                expansions.append({
                    "task_id": task.id,
                    "title": task.title,
                    "assignee": task.assignee,
                    "category": task.category,
                    "duration": task.duration,
                    "recurrence_type": task.recurrence_type,
                    "recurrence_interval": interval,
                    "occurrence_at": current.isoformat(),
                })
                current = current + step
                counter += 1
        expansions.sort(key=lambda item: item["occurrence_at"])
        return expansions

    # ── 库存管理 ────────────────────────────────────────────────────────

    async def list_inventory(self, user_id: int) -> list[InventoryItem]:
        """返回库存列表，按名称排序。"""
        statement = (
            select(InventoryItem)
            .where(InventoryItem.user_id == user_id)
            .order_by(InventoryItem.category, InventoryItem.name)
        )
        return list((await self._session.scalars(statement)).all())

    async def adjust_inventory(
        self,
        user_id: int,
        *,
        name: str,
        category: str = "未分类",
        delta: float,
        unit: str = "个",
        quantity: str | None = None,
        low_stock_threshold: float | None = None,
        note: str = "",
    ) -> InventoryItem:
        """按 (user_id, name) 增量调整库存：delta 为正入库、为负出库。

        不存在则新建；quantity_value 不会低于 0。返回调整后的库存项。
        """
        item = await self._session.scalar(
            select(InventoryItem).where(
                InventoryItem.user_id == user_id,
                InventoryItem.name == name,
            )
        )
        if item is None:
            item = InventoryItem(
                user_id=user_id,
                name=name,
                category=category,
                quantity=quantity or f"{max(delta, 0):g} {unit}",
                quantity_value=max(delta, 0.0),
                unit=unit,
                low_stock_threshold=low_stock_threshold or 0.0,
                note=note,
            )
            self._session.add(item)
        else:
            if category:
                item.category = category
            if unit:
                item.unit = unit
            if low_stock_threshold is not None:
                item.low_stock_threshold = low_stock_threshold
            if note:
                item.note = note
            item.quantity_value = max(item.quantity_value + delta, 0.0)
            item.quantity = quantity or f"{item.quantity_value:g} {item.unit}"
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def delete_inventory(self, item_id: int, user_id: int) -> bool:
        """删除指定库存项，严格校验用户归属。"""
        item = await self._session.scalar(
            select(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.user_id == user_id,
            )
        )
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.commit()
        return True

    async def restock_from_shopping(
        self, item: PlanShoppingItem, *, user_id: int
    ) -> InventoryItem:
        """采购项标记为已购买时入库：按数量解析数值后增量入库。"""
        parsed_value = _parse_quantity_value(item.quantity)
        return await self.adjust_inventory(
            user_id,
            name=item.name,
            category=item.category,
            delta=parsed_value,
            unit=_parse_quantity_unit(item.quantity) or "个",
            note=f"采购入库：{item.source}" if item.source else "采购入库",
        )


def _parse_quantity_value(quantity: str) -> float:
    """从数量字符串中解析数值部分，无法解析时按 1 计。"""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([^\d\s]+)?\s*", quantity)
    if match and match.group(1):
        return float(match.group(1))
    return 1.0


def _parse_quantity_unit(quantity: str) -> str | None:
    """从数量字符串中解析单位部分。"""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([^\d\s]+)?\s*", quantity)
    if match and match.group(2):
        return match.group(2)
    return None


def _recurrence_step(recurrence_type: str, interval: int) -> timedelta | None:
    """根据 recurrence_type 计算单步间隔。"""
    if recurrence_type == "daily":
        return timedelta(days=interval)
    if recurrence_type == "weekly":
        return timedelta(weeks=interval)
    if recurrence_type == "monthly":
        # 简化处理：按 30 天近似
        return timedelta(days=30 * interval)
    return None

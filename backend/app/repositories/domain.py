"""Repository for domain entities: recipes, expenses, and meal replacement.

去家庭化版：所有查询以 user_id 隔离。
Phase 3 清理：移除 PlanTask / TaskCompletion / InventoryItem 相关持久化
（plan_tasks / task_completions / inventory_items 表已删除）。
任务与库存不再落库；预算由 WeeklyPlan.budget 列承载，PlanBudget 表已删除。
"""

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExpenseRecord,
    PlanMealItem,
    PlanShoppingItem,
    RecipeRecord,
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
        groups: dict[tuple[str, str, str], list[PlanShoppingItem]] = defaultdict(list)
        for item in plan.shopping_items:
            normalized = "".join(item.name.lower().split())
            aliases = {"西红柿": "番茄", "土豆": "马铃薯", "鸡蛋": "蛋", "生抽酱油": "生抽"}
            normalized = aliases.get(normalized, normalized)
            # Extra purchases are independent procurement records.  They must
            # never be merged into a meal-derived ingredient merely by name.
            groups[(item.origin, normalized, item.category)].append(item)
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

    async def replace_meal(self, meal: PlanMealItem, **values: object) -> PlanMealItem:
        for key, value in values.items():
            setattr(meal, key, value)
        await self._session.commit()
        await self._session.refresh(meal)
        return meal

    async def add_shopping_item(
        self, user_id: int, **values: object
    ) -> PlanShoppingItem:
        """为活跃计划新增一条购物项（用于餐食替换后的清单联动）。"""
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        if plan is None:
            raise ValueError("没有活跃计划，无法添加购物项")
        item = PlanShoppingItem(plan_id=plan.id, **values)
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def remove_shopping_by_source(
        self, user_id: int, source: str
    ) -> list[PlanShoppingItem]:
        """删除来源标记为 ``source`` 的购物项，返回被删除的 ORM 对象列表。

        餐食替换只管理自己打标的清单项（``source == "餐食:{meal_id}"``），
        不触碰规划阶段生成或用户手工添加的条目，避免误删其他餐食共享的食材。
        """
        plan = await PlanningRepository(self._session).get_active_plan(user_id)
        if plan is None:
            return []
        removed = [item for item in plan.shopping_items if item.source == source]
        for item in removed:
            await self._session.delete(item)
        if removed:
            await self._session.commit()
        return removed

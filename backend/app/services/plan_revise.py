"""备餐规划局部修改服务。

把用户的自然语言修改要求经 LLM 解析为结构化 :class:`ReviseOperation`，
在内存中执行局部修改（不落库），返回 before/after :class:`PlanSnapshot`
与 :class:`PlanDiff` 预览。前端确认后再调用
``PlanningRepository.derive_plan_with_modifications`` 派生新版本。

设计要点：
- LLM 输出用 Pydantic :class:`ReviseOperation` 校验，失败重试最多 2 次
  （沿用 ``app/ai/llm.py`` 的 ``LLMGenerationError`` + ``bind(json_object)`` 模式）。
- 业务执行 ``apply_operation`` 是确定性计算——重算营养/购物/预算全部
  复用 :mod:`app.services.nutrition`，**不跑 11 节点 StateGraph**。
- Demo 模式（未配置真实 LLM）走关键词规则匹配兜底，保证测试可跑。
- 修改预览存到 ``ChatMessage.payload``（JSON 字段），不持久化计划本身——
  前端展示对话历史时可直接渲染。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMGenerationError
from app.ai.revision_workflow import run_revision_workflow
from app.core.config import Settings, get_settings
from app.models import ChatSession, RecipeRecord, WeeklyPlan
from app.repositories.conversations import ConversationRepository
from app.repositories.domain import DomainRepository
from app.schemas import (
    BudgetSnapshot,
    MealProposal,
    NutritionSnapshot,
    PlanDiff,
    PlanSnapshot,
    ReviseOperation,
    ReviseOperationType,
    RevisePreviewResponse,
)
from app.services.nutrition import (
    estimate_meal_nutrition,
    nutrition_delta,
)
from app.services.shopping_categories import normalize_shopping_category

# LLM 解析失败重试次数（首次 + 重试 = 最多 3 次调用）
_MAX_RETRIES = 2

# 关键词到 operation 的规则映射，仅 demo 模式使用
_DEMO_PATTERNS: list[tuple[str, ReviseOperationType, list[str]]] = [
    ("换成|替换|改吃|换为", "replace_meal", ["晚餐", "午餐", "早餐"]),
    ("不要|排除|去掉|别加|剔除", "exclude_ingredient", []),
    ("预算.*降|预算.*减|降到|减到", "update_budget", []),
    ("周末.*不做|周末.*外食|跳过.*天|不做饭", "skip_day", []),
    ("蛋白质|碳水|脂肪|热量.*提高|宏量", "adjust_macro_target", []),
    ("加一餐|添加.*餐|增加.*餐", "add_meal", []),
    ("去掉.*餐|删除.*餐|移除.*餐", "remove_meal", []),
]


def _weekday_from_text(text: str) -> str | None:
    """从中文文本里识别星期。"""
    mapping = {
        "周一": ["周一", "星期一", "周一"],
        "周二": ["周二", "星期二"],
        "周三": ["周三", "星期三"],
        "周四": ["周四", "星期四"],
        "周五": ["周五", "星期五"],
        "周六": ["周六", "星期六", "周末"],
        "周日": ["周日", "星期日", "周末"],
    }
    for weekday, aliases in mapping.items():
        for alias in aliases:
            if alias in text:
                return weekday
    return None


def _meal_type_from_text(text: str) -> str:
    """从中文文本里识别餐次。"""
    if "早" in text:
        return "早餐"
    if "午" in text:
        return "午餐"
    return "晚餐"


def _ingredient_from_text(text: str, candidates: list[str]) -> str | None:
    """从文本里提取食材名（demo 兜底，简单关键词匹配）。"""
    for candidate in candidates:
        if candidate in text:
            return candidate
    # 兜底：取"换成"后面的 2-4 字
    for marker in ("换成", "改吃", "换为", "替换为"):
        if marker in text:
            idx = text.index(marker) + len(marker)
            snippet = text[idx: idx + 6].strip("。，！的了")
            return snippet[:6] if snippet else None
    return None


def _parse_demo_operation(message: str, plan: WeeklyPlan) -> ReviseOperation | None:
    """Demo 模式下的关键词规则解析兜底。

    不调真实 LLM，仅基于正则关键词推断 operation 类型。
    覆盖测试场景和未配置 LLM 的本地开发。
    """
    text = message.strip()
    for pattern, op_type, _hints in _DEMO_PATTERNS:
        import re

        if not re.search(pattern, text):
            continue
        day = _weekday_from_text(text) or "周三"
        meal_type = _meal_type_from_text(text)

        if op_type == "replace_meal":
            ingredient = _ingredient_from_text(
                text, ["鸡胸肉", "牛肉", "鱼肉", "虾仁", "豆腐", "鸡蛋", "猪肉"]
            ) or "鸡胸肉"
            proposal = MealProposal(
                day=day,
                meal_type=meal_type,
                name=f"{ingredient}套餐",
                duration=25,
                cost=18.0,
                tags=[meal_type, "高蛋白"],
                reason=f"用户要求替换为{ingredient}",
                ingredients=[ingredient, "时蔬", "米饭"],
            )
            return ReviseOperation(
                operation=op_type,
                target={"day": day, "meal_type": meal_type},
                constraints={"main_ingredient": ingredient},
                proposal=proposal,
                reason=f"将{day}{meal_type}替换为{ingredient}主题餐",
            )
        if op_type == "exclude_ingredient":
            ingredient = _ingredient_from_text(
                text, ["牛奶", "鸡蛋", "海鲜", "花生", "香菜", "洋葱"]
            ) or "牛奶"
            return ReviseOperation(
                operation=op_type,
                target={"ingredient": ingredient},
                reason=f"从所有餐食与购物清单排除 {ingredient}",
            )
        if op_type == "update_budget":
            import re

            nums = re.findall(r"(\d+(?:\.\d+)?)\s*[元块]", text)
            budget = float(nums[0]) if nums else 300.0
            return ReviseOperation(
                operation=op_type,
                target={"budget_limit": budget},
                reason=f"将总预算调整为 {budget} 元",
            )
        if op_type == "skip_day":
            return ReviseOperation(
                operation=op_type,
                target={"day": day},
                reason=f"跳过 {day}，安排外食方案",
            )
        if op_type == "adjust_macro_target":
            import re

            nums = re.findall(r"(\d+(?:\.\d+)?)\s*[g克]", text)
            protein = float(nums[0]) if nums else 120.0
            return ReviseOperation(
                operation=op_type,
                target={"protein_g": protein},
                reason=f"将每日蛋白质目标调整为 {protein}g",
            )
        if op_type == "add_meal":
            ingredient = _ingredient_from_text(
                text, ["鸡胸肉", "牛肉", "鱼肉", "虾仁", "豆腐", "鸡蛋"]
            ) or "鸡蛋"
            proposal = MealProposal(
                day=day,
                meal_type=meal_type,
                name=f"加餐·{ingredient}",
                duration=15,
                cost=8.0,
                tags=[meal_type, "加餐"],
                reason="用户要求添加一餐",
                ingredients=[ingredient],
            )
            return ReviseOperation(
                operation=op_type,
                target={"day": day, "meal_type": meal_type},
                proposal=proposal,
                reason=f"在 {day} 增加一餐 {ingredient}",
            )
        if op_type == "remove_meal":
            return ReviseOperation(
                operation=op_type,
                target={"day": day, "meal_type": meal_type},
                reason=f"移除 {day} 的{meal_type}",
            )
    return None


class PlanReviseService:
    """备餐规划局部修改服务。

    生命周期：
        1. :meth:`generate_preview` —— LLM 解析 + 业务执行 + diff，返回预览。
        2. 前端展示 ``before/after``，用户在对话区确认或继续提新要求。
        3. :meth:`confirm` —— 调 ``derive_plan_with_modifications`` 落库新版本。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # .bind(response_format=...) 返回 RunnableBinding，类型放宽到 Any
        self._llm_model: Any = None
        if self._settings.real_llm_enabled:
            self._llm_model = ChatOpenAI(
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url,
                model=self._settings.llm_model,
                temperature=0.1,
                timeout=self._settings.llm_timeout_seconds,
                max_retries=1,
                max_tokens=2048,
            ).bind(response_format={"type": "json_object"})

    # ── 公开 API ────────────────────────────────────────────────────

    async def generate_preview(
        self,
        plan: WeeklyPlan,
        message: str,
        session: AsyncSession,
        user_id: int,
        chat_session_id: str | None = None,
    ) -> RevisePreviewResponse:
        """生成修改预览。

        Args:
            plan: 当前要修改的计划（含已加载的 meals/shopping_items）。
            message: 用户的自然语言修改要求。
            session: 数据库会话，用于查 recipes 做营养估算 + 存对话消息。
            user_id: 当前用户 ID。
            chat_session_id: 可选的对话会话 ID；缺省按 plan 复用最近或新建。

        Returns:
            :class:`RevisePreviewResponse`，``revise_id`` 用于后续 confirm。
        """
        operation = await self._parse_operation(message, plan, session, user_id)
        recipes = await DomainRepository(session).list_recipes(user_id)
        before = self._snapshot(plan, recipes)
        routing, applied = run_revision_workflow(
            plan, operation, recipes, self._apply_operation
        )
        after_meals, after_shopping, after_budget, diff = applied
        after = self._snapshot_from(after_meals, after_shopping, after_budget, recipes)
        if after.budget.estimated > after.budget.limit:
            diff.conflict_warnings.append(
                f"调整后预计超出预算 {after.budget.estimated - after.budget.limit:.1f} 元"
            )
        if len(after.meals) != 21:
            diff.conflict_warnings.append(
                f"调整后共有 {len(after.meals)} 餐，与完整周计划 21 餐不一致"
            )
        diff.nutrition_delta = nutrition_delta(
            before.nutrition.model_dump(), after.nutrition.model_dump()
        )
        diff.budget_delta = {
            "limit": round(after.budget.limit - before.budget.limit, 2),
            "estimated": round(after.budget.estimated - before.budget.estimated, 2),
            "saved": round(after.budget.saved - before.budget.saved, 2),
        }
        can_confirm = before.model_dump(mode="json") != after.model_dump(mode="json")
        summary = self._summarize(operation)

        # 存对话消息（用户消息 + 助手预览消息），保留完整历史
        repository = ConversationRepository(session)
        chat_session = await self._resolve_chat_session(
            repository, chat_session_id, user_id, plan.id
        )
        await repository.add_message(
            chat_session, role="user", content=message
        )
        assistant = await repository.add_message(
            chat_session,
            role="assistant",
            content=summary,
            payload={
                "kind": "plan_revise_preview",
                "plan_id": plan.id,
                "plan_version": plan.version,
                "operation": operation.model_dump(mode="json"),
                "routing": routing.model_dump(mode="json"),
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
                "diff": diff.model_dump(mode="json"),
                # 修改后的子项列表，confirm 时直接落库（避免重复计算）
                "modified_meals": after_meals,
                "modified_shopping": after_shopping,
                "modified_budget": after_budget,
                "can_confirm": can_confirm,
                "message": message,
            },
        )

        return RevisePreviewResponse(
            revise_id=str(assistant.id),
            plan_id=plan.id,
            plan_version=plan.version,
            operation=operation,
            routing=routing,
            summary=summary,
            before=before,
            after=after,
            diff=diff,
            can_confirm=can_confirm,
            message_id=assistant.id,
        )

    # ── LLM 解析 ────────────────────────────────────────────────────

    async def _parse_operation(
        self,
        message: str,
        plan: WeeklyPlan,
        session: AsyncSession,
        user_id: int,
    ) -> ReviseOperation:
        """LLM 解析 + Pydantic 校验 + 失败重试。"""
        if self._llm_model is None:
            op = _parse_demo_operation(message, plan)
            if op is None:
                raise LLMGenerationError(
                    "Demo 模式下无法解析该修改要求，请配置真实 LLM 或换一种表述"
                )
            return op

        plan_summary = self._plan_brief(plan)
        schema = ReviseOperation.model_json_schema()
        system_prompt = (
            "你是 SoloChef 备餐修改智能体。把用户的自然语言修改要求解析为结构化 JSON 指令。"
            "只输出符合 schema 的 JSON 对象，不要添加 Markdown 代码块或解释。"
            "operation 必须是七种之一：replace_meal/remove_meal/add_meal/"
            "exclude_ingredient/update_budget/skip_day/adjust_macro_target。"
            "target.day 用中文（周一~周日）。proposal 仅在 replace_meal/add_meal 时提供。"
        )
        user_prompt = (
            f"当前计划摘要：\n{plan_summary}\n\n"
            f"用户修改要求：{message}\n\n"
            f"严格按下面的 JSON Schema 输出：\n{json.dumps(schema, ensure_ascii=False)}"
        )
        last_error: LLMGenerationError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                parts: list[str] = []
                async for chunk in self._llm_model.astream(
                    [("system", system_prompt), ("user", user_prompt)]
                ):
                    chunk_content = chunk.content
                    content = (
                        chunk_content if isinstance(chunk_content, str) else str(chunk_content)
                    )
                    if content:
                        parts.append(content)
                raw = "".join(parts)
                return ReviseOperation.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = LLMGenerationError(
                    f"LLM 修改指令解析失败 (attempt {attempt + 1}/{_MAX_RETRIES + 1}): "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = LLMGenerationError(
                    f"LLM 请求失败 (attempt {attempt + 1}): {type(exc).__name__}: {str(exc)[:300]}"
                )
        raise last_error or LLMGenerationError("LLM 修改指令解析失败")

    # ── 业务执行 ────────────────────────────────────────────────────

    def _apply_operation(
        self,
        plan: WeeklyPlan,
        operation: ReviseOperation,
        recipes: list[RecipeRecord],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, PlanDiff]:
        """在内存中应用修改操作，返回 (新meals, 新shopping, 新budget, diff)。

        不修改 plan 对象本身——调用方拿到的都是新的列表。
        """
        meals = [self._meal_to_dict(m) for m in plan.meals]
        original_meals = [dict(meal) for meal in meals]
        shopping = [self._shopping_to_dict(s) for s in plan.shopping_items]
        budget = plan.budget
        diff = PlanDiff()
        op = operation.operation

        if op == "replace_meal" and operation.proposal is not None:
            target_day = operation.target.get("day", operation.proposal.day)
            target_meal_type = operation.target.get("meal_type", operation.proposal.meal_type)
            idx = self._find_meal_index(meals, target_day, target_meal_type)
            if idx is not None:
                old_name = meals[idx]["name"]
                new_dict = operation.proposal.to_meal_item_dict()
                meals[idx] = new_dict
                diff.changed_meals.append(
                    f"{target_day}{target_meal_type or ''}: {old_name} → {new_dict['name']}"
                )
            else:
                diff.conflict_warnings.append(
                    f"未在 {target_day} 找到 {target_meal_type}，已作为新餐食追加"
                )
                meals.append(operation.proposal.to_meal_item_dict())

        elif op == "add_meal" and operation.proposal is not None:
            meals.append(operation.proposal.to_meal_item_dict())
            diff.changed_meals.append(
                f"{operation.proposal.day}: 新增 {operation.proposal.name}"
            )

        elif op == "remove_meal":
            target_day = operation.target.get("day")
            target_meal_type = operation.target.get("meal_type")
            idx = self._find_meal_index(meals, target_day, target_meal_type)
            if idx is not None:
                removed_name = meals[idx]["name"]
                meals.pop(idx)
                diff.changed_meals.append(
                    f"{target_day}{target_meal_type or ''}: 移除 {removed_name}"
                )
            else:
                diff.conflict_warnings.append(f"{target_day} 未找到匹配餐食")

        elif op == "exclude_ingredient":
            ingredient = operation.target.get("ingredient", "")
            if not ingredient:
                diff.conflict_warnings.append("exclude_ingredient 缺少 ingredient 目标")
            else:
                affected_meals: list[str] = []
                for meal in meals:
                    if any(ingredient in ing for ing in meal["ingredients"]):
                        meal["ingredients"] = [
                            ing for ing in meal["ingredients"] if ingredient not in ing
                        ]
                        affected_meals.append(f"{meal['day']}·{meal['name']}")
                shopping = [s for s in shopping if ingredient not in s["name"]]
                diff.changed_meals.append(f"从 {len(affected_meals)} 餐移除 {ingredient}")
                diff.changed_shopping.append(f"购物清单移除含 {ingredient} 的商品")

        elif op == "update_budget":
            new_limit = float(operation.target.get("budget_limit", budget))
            budget = new_limit
            diff.changed_meals.append(f"预算上限调整：{plan.budget} → {new_limit} 元")

        elif op == "skip_day":
            day = operation.target.get("day", "")
            before_count = len(meals)
            meals = [m for m in meals if m["day"] != day]
            removed = before_count - len(meals)
            diff.changed_meals.append(f"{day}: 跳过本天，移除 {removed} 餐")
            diff.conflict_warnings.append(f"{day} 改为外食方案，需用户自行安排")

        elif op == "adjust_macro_target":
            # 仅记录目标调整意图，实际改 NutritionGoal 需要单独 API（避免在本服务内做太多）
            target_desc = ", ".join(
                f"{k}={v}" for k, v in operation.target.items() if v is not None
            )
            diff.conflict_warnings.append(
                f"营养目标调整（{target_desc}）需在营养目标页确认后重算计划，"
                "本次预览不直接应用。"
            )

        if op in {"replace_meal", "add_meal", "remove_meal", "skip_day"}:
            shopping, shopping_changes = self._reconcile_shopping_with_meals(
                shopping, original_meals, meals
            )
            diff.changed_shopping.extend(shopping_changes)

        # budget_delta 在调用方算（需要 before/after 两个 snapshot）
        return meals, shopping, budget, diff

    # ── 工具方法 ────────────────────────────────────────────────────

    @staticmethod
    def _reconcile_shopping_with_meals(
        shopping: list[dict[str, Any]],
        before_meals: list[dict[str, Any]],
        after_meals: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Synchronize shopping items from the changed meal ingredient set."""

        before_ingredients = {
            ingredient.strip()
            for meal in before_meals
            for ingredient in meal.get("ingredients", [])
            if ingredient.strip()
        }
        after_ingredients = {
            ingredient.strip()
            for meal in after_meals
            for ingredient in meal.get("ingredients", [])
            if ingredient.strip()
        }
        added = after_ingredients - before_ingredients
        removed = before_ingredients - after_ingredients
        changes: list[str] = []

        retained: list[dict[str, Any]] = []
        for item in shopping:
            if item.get("name") in removed:
                changes.append(f"移除不再需要的购物项：{item['name']}")
                continue
            retained.append(item)

        existing_names = {str(item.get("name") or "") for item in retained}
        source_days = sorted(
            {meal.get("day", "") for meal in after_meals if meal.get("day")}
        )
        source = " / ".join(source_days) or "计划调整"
        for ingredient in sorted(added):
            if ingredient in existing_names:
                continue
            retained.append(
                {
                    "id": 0,
                    "name": ingredient,
                    "category": "其他",
                    "quantity": "1份",
                    "price": 0.0,
                    "source": source,
                    "purchased": False,
                }
            )
            changes.append(f"新增关联购物项：{ingredient}")
        return retained, changes

    @staticmethod
    def _find_meal_index(
        meals: list[dict[str, Any]],
        target_day: str | None,
        target_meal_type: str | None,
    ) -> int | None:
        """定位餐食索引。

        匹配优先级：
        1. day + meal_type 精确匹配（name 或 tags 含 meal_type 关键词）
        2. 该天只有一餐 → 直接返回（meal_type 在数据未标时也兜底）
        3. 该天第一餐（兜底，记录 warning 由调用方追加）
        """
        if not target_day:
            return None
        day_indexes = [i for i, m in enumerate(meals) if m.get("day") == target_day]
        if not day_indexes:
            return None
        if target_meal_type:
            for i in day_indexes:
                if meals[i].get("meal_type") == target_meal_type:
                    return i
            for i in day_indexes:
                meal = meals[i]
                name = meal.get("name", "")
                tags = meal.get("tags") or []
                if target_meal_type in name or any(target_meal_type in t for t in tags):
                    return i
        # 该天只有一餐 → 直接返回（兼容 demo 数据未标 meal_type 的情况）
        if len(day_indexes) == 1:
            return day_indexes[0]
        # 多餐时 fallback 到该天第一餐
        return day_indexes[0]

    @staticmethod
    def _meal_to_dict(meal: Any) -> dict[str, Any]:
        """ORM PlanMealItem → dict（保留 id 用于 override 估算）。"""
        return {
            "id": getattr(meal, "id", 0),
            "day": meal.day,
            "meal_type": meal.meal_type,
            "name": meal.name,
            "duration": meal.duration,
            "cost": meal.cost,
            "tags": list(meal.tags or []),
            "reason": meal.reason or "",
            "ingredients": list(meal.ingredients or []),
        }

    @staticmethod
    def _shopping_to_dict(item: Any) -> dict[str, Any]:
        """ORM PlanShoppingItem → dict。"""
        return {
            "id": getattr(item, "id", 0),
            "name": item.name,
            "category": item.category,
            "quantity": item.quantity,
            "price": item.price,
            "source": item.source or "",
            "purchased": item.purchased,
        }

    def _snapshot(
        self, plan: WeeklyPlan, recipes: list[RecipeRecord]
    ) -> PlanSnapshot:
        """从 ORM plan 构建 PlanSnapshot。"""
        meals_dicts = [self._meal_to_dict(m) for m in plan.meals]
        shopping_dicts = [self._shopping_to_dict(s) for s in plan.shopping_items]
        return self._snapshot_from(meals_dicts, shopping_dicts, plan.budget, recipes)

    def _snapshot_from(
        self,
        meals: list[dict[str, Any]],
        shopping: list[dict[str, Any]],
        budget: float,
        recipes: list[RecipeRecord],
    ) -> PlanSnapshot:
        """从 dict 列表构建 PlanSnapshot（用于 after 场景）。"""
        nutrition = self._sum_nutrition(meals, recipes)
        # 预算只按采购清单计价。餐食 cost 是菜单估算，不得重复相加。
        estimated = sum(float(s.get("price", 0)) for s in shopping)
        # 简单分类汇总（按购物 category）
        categories: dict[str, float] = {}
        for s in shopping:
            cat = normalize_shopping_category(s.get("category"), s.get("name", ""))
            categories[cat] = round(categories.get(cat, 0) + float(s.get("price", 0)), 2)
        return PlanSnapshot(
            meals=meals,
            shopping=shopping,
            nutrition=NutritionSnapshot(**nutrition),
            budget=BudgetSnapshot(
                limit=float(budget),
                estimated=round(estimated, 2),
                saved=round(float(budget) - estimated, 2),
                usage_percent=int(round(estimated / float(budget) * 100)) if budget else 0,
                categories=categories,
            ),
        )

    @staticmethod
    def _sum_nutrition(
        meals: list[dict[str, Any]], recipes: list[RecipeRecord]
    ) -> dict[str, float]:
        """汇总一组 dict 餐食的营养（复用 nutrition.estimate_meal_nutrition）。"""

        class _MealLike:
            """适配 nutrition.estimate_meal_nutrition 的 Protocol。"""

            def __init__(self, name: str, ingredients: list[str]) -> None:
                self.name = name
                self.ingredients = ingredients

        total: dict[str, float] = {}
        for meal in meals:
            meal_like = _MealLike(meal.get("name", ""), meal.get("ingredients", []))
            nutrition, _ = estimate_meal_nutrition(meal_like, recipes)
            for key, value in nutrition.items():
                total[key] = round(total.get(key, 0.0) + value, 1)
        return total

    def _plan_brief(self, plan: WeeklyPlan) -> str:
        """给 LLM 的计划摘要（控制 token 量，避免 7 天全量 JSON 爆炸）。"""
        meals_brief = "\n".join(
            f"- {m.day}·{m.name}（食材: {', '.join(m.ingredients[:5])}，成本 {m.cost}元）"
            for m in plan.meals[:14]  # 最多列 14 餐
        )
        shopping_brief = ", ".join(s.name for s in plan.shopping_items[:10])
        return (
            f"版本 v{plan.version}, 预算 {plan.budget}元\n"
            f"餐食：\n{meals_brief}\n"
            f"购物清单: {shopping_brief}\n"
        )

    @staticmethod
    def _summarize(operation: ReviseOperation) -> str:
        """生成人类可读的修改说明，作为对话气泡文本。"""
        op = operation.operation
        target = operation.target
        if op == "replace_meal" and operation.proposal:
            return (
                f"已将 {target.get('day', '')}{target.get('meal_type', '')} "
                f"替换为「{operation.proposal.name}」（含 "
                f"{', '.join(operation.proposal.ingredients[:4])}），"
                f"请确认下方差异。"
            )
        if op == "add_meal" and operation.proposal:
            return (
                f"已在 {operation.proposal.day} 添加「{operation.proposal.name}」，"
                f"请确认差异。"
            )
        if op == "remove_meal":
            return f"已移除 {target.get('day', '')}{target.get('meal_type', '')}，请确认。"
        if op == "exclude_ingredient":
            return f"已从所有餐食与购物清单排除「{target.get('ingredient', '')}」。"
        if op == "update_budget":
            return f"已将预算调整为 {target.get('budget_limit', '')} 元。"
        if op == "skip_day":
            return f"已跳过 {target.get('day', '')}，请自行安排外食。"
        if op == "adjust_macro_target":
            return (
                f"已记录营养目标调整意图（{target}），需在营养目标页确认。"
            )
        return f"已处理修改请求：{operation.reason or op}"

    async def _resolve_chat_session(
        self,
        repository: ConversationRepository,
        chat_session_id: str | None,
        user_id: int,
        plan_id: int,
    ) -> ChatSession:
        """复用指定会话或按 plan_id 复用最近会话或新建。

        标题中带 plan_id 标记，便于后续按计划检索对话历史。
        """
        if chat_session_id:
            session = await repository.get_session(chat_session_id, user_id)
            if session is not None:
                return session
        # 按标题前缀找该 plan 的最近会话
        title_prefix = f"[计划v{plan_id}]"
        sessions = await repository.list_sessions(user_id)
        for s in sessions:
            if s.title.startswith(title_prefix):
                return s
        return await repository.create_session(
            user_id, f"[计划v{plan_id}] 备餐修改对话"
        )


plan_revise_service = PlanReviseService()

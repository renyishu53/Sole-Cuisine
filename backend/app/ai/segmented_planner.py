"""Planner 分段生成

将单次 LLM 调用（meals+shopping+budget 一体）拆分为 3 个阶段：

1. **meals 阶段**：基于用户需求 + RAG 上下文生成一周菜单
2. **shopping 阶段**：基于 meals 结果推导采购清单（减少幻觉）
3. **budget 阶段**：基于 meals + shopping 计算预算分配

每阶段使用聚焦的小 prompt，提升单阶段质量；任一阶段失败时回退到
单次生成模式（向后兼容）。通过 ``settings.planner_segmented_enabled``
开关启用，默认关闭——当前 Verifier 兜底已够用，分段生成为可选增强。

设计原则：
- 实现 ``PlanGenerator`` 协议，对上层 ``SoloChefWorkflow`` 透明
- 复用现有 ``PlanDraft`` schema，不改变输出结构
- 每阶段独立 JSON 解析与校验，失败时提供精确的错误定位
- 流式 token sink 在每阶段独立工作，前端仍能看到逐步输出
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.ai.llm import (
    LLMGenerationError,
    PlanDraft,
    PlanGenerator,
    token_sink,
    validate_weekly_meals,
    with_meal_types,
)
from app.core.config import Settings
from app.schemas import PlanningRequest
from app.schemas.domain import BudgetSummary, MealItem, ShoppingItem, TaskItem

logger = logging.getLogger(__name__)


# ── 分段生成的中间结果 schema ────────────────────────────────────────────


class MealsStage(BaseModel):
    """meals 阶段输出：一周菜单 + 摘要。"""

    summary: str
    meals: list[MealItem]


class ShoppingStage(BaseModel):
    """shopping 阶段输出：采购清单（基于 meals 推导）。"""

    shopping: list[ShoppingItem]


class BudgetStage(BaseModel):
    """budget 阶段输出：预算分配（基于 meals + shopping 计算）。"""

    budget: BudgetSummary


# ── 分段 prompt 模板 ─────────────────────────────────────────────────────

_MEALS_SYSTEM = (
    "你是 SoloChef 餐食规划智能体。只依据用户需求和 RAG 上下文生成一周菜单。"
    "严格遵守成员忌口和营养目标。只输出 JSON 对象，不添加 Markdown 或解释。"
)

_MEALS_USER = (
    "用户需求：{prompt}\n预算上限：{budget}\n"
    "Graph RAG 上下文：\n{context}\n\n"
    "请生成周一至周日 21 餐的菜单（每天早餐、午餐、晚餐各 1 餐，共 7×3=21 餐）。每餐必须含 name/day/meal_type/duration/cost/tags/ingredients/reason；meal_type 只能是 早餐、午餐或晚餐。"
    "duration 为整数（分钟），cost 为数字（元），ingredients 列出主要食材。\n"
    "只输出 {{\"summary\": \"...\", \"meals\": [...]}} 格式的 JSON。"
)

_SHOPPING_SYSTEM = (
    "你是 SoloChef 采购规划智能体。根据已确定的菜单推导采购清单，不要编造菜单外的食材。"
    "只输出 JSON 对象。"
)

_SHOPPING_USER = (
    "已确定的一周菜单：\n{meals_json}\n\n"
    "请根据菜单中的 ingredients 推导采购清单，合并同类项。"
    "每项含 name/category/quantity/price/source/purchased(false)。category 只能是：肉蛋奶、蔬菜、主食、水果、其他。\n"
    "只输出 {{\"shopping\": [...]}} 格式的 JSON。"
)

_BUDGET_SYSTEM = (
    "你是 SoloChef 预算规划智能体。根据菜单和采购清单计算预算分配。"
    "只输出 JSON 对象。"
)

_BUDGET_USER = (
    "预算上限：{budget}\n采购清单：\n{shopping_json}\n\n"
    "请计算预算分配。含 limit/estimated/saved/usage_percent/categories。\n"
    "estimated 为采购清单总价，categories 按分类汇总。\n"
    "只输出 {{\"budget\": {{...}}}} 格式的 JSON。"
)

# 分段失败时的兜底 tasks（与 DemoPlanGenerator 一致的空任务列表）
_EMPTY_TASKS: list[TaskItem] = []


class SegmentedPlanGenerator:
    """分段生成 PlanDraft：meals → shopping → budget 三阶段。

    通过 ``settings.planner_segmented_enabled`` 启用。任一阶段失败时
    回退到 ``OpenAICompatiblePlanGenerator`` 的单次生成模式，保证向后兼容。

    实现 ``PlanGenerator`` 协议，对 ``SoloChefWorkflow`` 透明——上层无需
    感知分段逻辑，只需将 ``generator`` 替换为本类实例。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=0.2,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
            max_tokens=2048,  # 分段后单次输出更小，减半避免浪费
        ).bind(response_format={"type": "json_object"})
        # 延迟导入避免循环依赖
        from app.ai.llm import OpenAICompatiblePlanGenerator

        self._fallback = OpenAICompatiblePlanGenerator(settings)

    @property
    def mode(self) -> str:
        return f"{self._settings.llm_provider}-segmented"

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
        """三阶段生成，任一阶段失败回退到单次模式。"""
        try:
            return await self._generate_segmented(request, context)
        except LLMGenerationError as exc:
            logger.warning(
                "分段生成失败 (%s)，回退到单次生成模式", type(exc).__name__
            )
            return await self._fallback.generate(request, context)

    async def _generate_segmented(
        self, request: PlanningRequest, context: str
    ) -> PlanDraft:
        """执行三阶段生成，失败时抛 LLMGenerationError 由上层回退。"""
        # ── 阶段 1：meals ──
        meals_stage = await self._call_stage(
            system=_MEALS_SYSTEM,
            user=_MEALS_USER.format(
                prompt=request.prompt,
                budget=request.budget,
                context=context,
            ),
            schema_cls=MealsStage,
            stage_name="meals",
        )
        meals_stage.meals = with_meal_types(meals_stage.meals)
        validate_weekly_meals(meals_stage.meals)

        # ── 阶段 2：shopping（基于 meals 推导）──
        meals_json = json.dumps(
            [meal.model_dump(mode="json") for meal in meals_stage.meals],
            ensure_ascii=False,
        )
        shopping_stage = await self._call_stage(
            system=_SHOPPING_SYSTEM,
            user=_SHOPPING_USER.format(meals_json=meals_json),
            schema_cls=ShoppingStage,
            stage_name="shopping",
        )

        # ── 阶段 3：budget（基于 shopping 计算）──
        shopping_json = json.dumps(
            [item.model_dump(mode="json") for item in shopping_stage.shopping],
            ensure_ascii=False,
        )
        budget_stage = await self._call_stage(
            system=_BUDGET_SYSTEM,
            user=_BUDGET_USER.format(
                budget=request.budget,
                shopping_json=shopping_json,
            ),
            schema_cls=BudgetStage,
            stage_name="budget",
        )

        return PlanDraft(
            summary=meals_stage.summary,
            meals=meals_stage.meals,
            shopping=shopping_stage.shopping,
            tasks=list(_EMPTY_TASKS),
            budget=budget_stage.budget,
            conflicts=[],
        )

    async def _call_stage(
        self,
        *,
        system: str,
        user: str,
        schema_cls: type[BaseModel],
        stage_name: str,
    ) -> Any:
        """执行单阶段 LLM 调用并解析 JSON，失败抛 LLMGenerationError。"""
        import asyncio

        parts: list[str] = []
        try:
            async for chunk in self._model.astream(
                [("system", system), ("user", user)]
            ):
                content = (
                    chunk.content
                    if isinstance(chunk.content, str)
                    else str(chunk.content)
                )
                if not content:
                    continue
                parts.append(content)
                sink = token_sink.get()
                if sink is not None:
                    await sink(content)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise LLMGenerationError(
                f"分段生成 [{stage_name}] LLM 请求失败 "
                f"({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc

        content = "".join(parts)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMGenerationError(
                f"分段生成 [{stage_name}] 返回的不是有效 JSON "
                f"({exc.msg}, position={exc.pos})"
            ) from exc

        try:
            return schema_cls.model_validate(data)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors()[:3]
            )
            raise LLMGenerationError(
                f"分段生成 [{stage_name}] JSON 不符合 Schema ({errors})"
            ) from exc


def build_segmented_generator(settings: Settings) -> PlanGenerator:
    """构建分段生成器，未启用真实 LLM 时回退到 demo 模式。"""
    if not settings.real_llm_enabled:
        from app.ai.llm import DemoPlanGenerator

        return DemoPlanGenerator()
    return SegmentedPlanGenerator(settings)

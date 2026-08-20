import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from time import perf_counter
from typing import Protocol

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.schemas import LLMSmokeResponse, PlanningRequest
from app.schemas.domain import BudgetSummary, MealItem, ShoppingItem, TaskItem
from app.services.demo_data import MEALS, SHOPPING, TASKS

TokenSink = Callable[[str], Awaitable[None]]
token_sink: ContextVar[TokenSink | None] = ContextVar("solochef_token_sink", default=None)

WEEK_DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
MEAL_TYPES = ("早餐", "午餐", "晚餐")


class LLMGenerationError(RuntimeError):
    """Raised when the configured model cannot return a valid plan."""


class PlanDraft(BaseModel):
    summary: str
    meals: list[MealItem]
    shopping: list[ShoppingItem]
    tasks: list[TaskItem]
    budget: BudgetSummary
    conflicts: list[str]


class PlanGenerator(Protocol):
    @property
    def mode(self) -> str: ...

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft: ...


def with_meal_types(meals: list[MealItem]) -> list[MealItem]:
    """Fill meal types for legacy/demo meals that encode the type in their names."""
    typed: list[MealItem] = []
    for meal in meals:
        meal_type = next((item for item in MEAL_TYPES if meal.name.startswith(item)), meal.meal_type)
        typed.append(meal.model_copy(update={"meal_type": meal_type}))
    return typed


def validate_weekly_meals(meals: list[MealItem]) -> None:
    """Require exactly breakfast, lunch and dinner for every day of a weekly plan."""
    if len(meals) != len(WEEK_DAYS) * len(MEAL_TYPES):
        raise LLMGenerationError(f"weekly plan must contain 21 meals, got {len(meals)}")
    slots = {(meal.day, meal.meal_type) for meal in meals}
    expected = {(day, meal_type) for day in WEEK_DAYS for meal_type in MEAL_TYPES}
    if slots != expected:
        missing = sorted(expected - slots)
        duplicate_count = len(meals) - len(slots)
        raise LLMGenerationError(
            f"weekly plan must cover every breakfast/lunch/dinner slot; missing={missing}, duplicates={duplicate_count}"
        )


class DemoPlanGenerator:
    @property
    def mode(self) -> str:
        return "demo"

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
        del context
        estimated = min(472.0, request.budget * 0.94)
        return PlanDraft(
            summary="已结合营养目标、预算约束和检索上下文生成一周膳食计划。",
            meals=with_meal_types(MEALS),
            shopping=SHOPPING,
            tasks=TASKS,
            budget=BudgetSummary(
                limit=request.budget,
                estimated=estimated,
                saved=request.budget - estimated,
                usage_percent=round(estimated / request.budget * 100),
                categories={
                    "肉蛋奶": min(188, estimated * 0.4),
                    "蔬菜": min(112, estimated * 0.24),
                    "主食": min(68, estimated * 0.15),
                    "其他": max(0, estimated - 368),
                },
            ),
            conflicts=[],
        )


class OpenAICompatiblePlanGenerator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=0.2,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
            max_tokens=4096,
        ).bind(response_format={"type": "json_object"})

    @property
    def mode(self) -> str:
        return self._settings.llm_provider

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
        system_prompt = (
            "你是 SoloChef 规划协调智能体。只能依据用户需求和 RAG 上下文生成计划。"
            "严格遵守成员忌口、营养目标和预算，不要编造来源。只输出符合要求的 JSON 对象。"
        )
        user_prompt = (
            f"用户需求：{request.prompt}\n预算上限：{request.budget}\n"
            f"Graph RAG 上下文：\n{context}\n\n"
            "请严格按照下面的 JSON Schema 输出一个 JSON 对象，不得添加 Markdown 代码块、"
            "解释文字或 Schema 之外的字段。meals 必须恰好包含 21 项（周一至周日每天早餐、午餐、晚餐各 1 项，共 7×3=21），"
            "每项必须明确填写 meal_type，且只能为 早餐、午餐、晚餐；"
            "所有 id 和 duration 必须是整数，金额必须是数字，任务 status 只能是"
            " todo、doing 或 done。\nJSON Schema：\n"
            f"{json.dumps(PlanDraft.model_json_schema(), ensure_ascii=False)}"
        )
        try:
            parts: list[str] = []
            async for chunk in self._model.astream(
                [("system", system_prompt), ("user", user_prompt)]
            ):
                content = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
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
                f"LLM 请求失败 ({type(exc).__name__}: {str(exc)[:300]})"
            ) from exc
        content = "".join(parts)
        try:
            draft = PlanDraft.model_validate(json.loads(content))
            draft.meals = with_meal_types(draft.meals)
            validate_weekly_meals(draft.meals)
            return draft
        except json.JSONDecodeError as exc:
            raise LLMGenerationError(
                f"LLM 返回的内容不是有效 JSON ({exc.msg}, position={exc.pos})"
            ) from exc
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()[:5]
            )
            raise LLMGenerationError(f"LLM JSON 不符合计划 Schema ({errors})") from exc


def build_plan_generator(settings: Settings) -> PlanGenerator:
    # P3 分段生成：启用时返回 SegmentedPlanGenerator，否则走单次生成
    if settings.planner_segmented_enabled and settings.real_llm_enabled:
        from app.ai.segmented_planner import SegmentedPlanGenerator

        return SegmentedPlanGenerator(settings)
    if settings.real_llm_enabled:
        return OpenAICompatiblePlanGenerator(settings)
    return DemoPlanGenerator()


async def smoke_test_llm(settings: Settings) -> LLMSmokeResponse:
    """Perform one minimal real-provider call without exposing credentials."""
    if not settings.real_llm_enabled:
        raise LLMGenerationError("未配置真实 LLM_PROVIDER 和 LLM_API_KEY")
    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
        max_tokens=16,
    )
    started = perf_counter()
    try:
        response = await model.ainvoke([("system", "只回复 SOLOCHEF_LLM_OK"), ("user", "连接测试")])
    except Exception as exc:
        raise LLMGenerationError(f"真实 LLM 冒烟调用失败: {type(exc).__name__}") from exc
    content = response.content if isinstance(response.content, str) else str(response.content)
    return LLMSmokeResponse(
        status="connected",
        provider=settings.llm_provider,
        model=settings.llm_model,
        latency_ms=max(1, round((perf_counter() - started) * 1000)),
        message=content[:200],
    )


class ChatAssistant:
    """通用对话助手：自然语言输出，不强制 JSON，支持流式。

    与 OpenAICompatiblePlanGenerator 的区别：
    - 不绑定 response_format=json_object，允许自然语言回答
    - temperature 更高（0.6），回答更自然
    - 不走 PlanDraft schema 校验，直接流式输出文本
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: ChatOpenAI | None = None

    def _get_model(self) -> ChatOpenAI:
        if self._model is None:
            self._model = ChatOpenAI(
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url,
                model=self._settings.llm_model,
                temperature=0.6,
                timeout=max(self._settings.llm_timeout_seconds, 90.0),
                max_retries=1,
                max_tokens=2048,
            )
        return self._model

    async def answer(
        self,
        question: str,
        context: str = "",
        rag_snippets: list[str] | None = None,
        history: list[tuple[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """流式返回自然语言回答。

        Args:
            question: 用户当前问题
            context: 只读上下文（画像/目标/计划摘要）
            rag_snippets: RAG 检索到的知识片段
            history: 多轮历史 [(role, content), ...]，仅 user/assistant
        """
        system_parts = [
            "你是 SoloChef 营养助手，专注回答饮食、食谱、购物、热量计算等问题。",
            "基于用户画像、营养目标和当前计划给出个性化建议。",
            "不要生成完整周计划，不要输出 JSON，用自然语言清晰回答。",
            "如果问题超出饮食范围，礼貌引导回饮食话题。",
        ]
        if context:
            system_parts.append(f"\n用户上下文：\n{context}")
        if rag_snippets:
            system_parts.append("\n知识库参考：\n" + "\n---\n".join(rag_snippets))

        messages: list[tuple[str, str]] = [("system", "\n".join(system_parts))]
        if history:
            messages.extend(history[-8:])
        messages.append(("user", question))

        model = self._get_model()
        try:
            async for chunk in model.astream(messages):
                content = (
                    chunk.content
                    if isinstance(chunk.content, str)
                    else str(chunk.content)
                )
                if content:
                    yield content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise LLMGenerationError(
                f"对话 LLM 请求失败 ({type(exc).__name__}: {str(exc)[:300]})"
            ) from exc


_chat_assistant: ChatAssistant | None = None


def get_chat_assistant() -> ChatAssistant | None:
    """懒加载对话助手单例。未配置真实 LLM 时返回 None，调用方需做 None 检查。"""
    global _chat_assistant
    if _chat_assistant is None:
        settings = get_settings()
        if settings.real_llm_enabled:
            _chat_assistant = ChatAssistant(settings)
    return _chat_assistant

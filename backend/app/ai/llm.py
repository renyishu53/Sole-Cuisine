import asyncio
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from time import perf_counter
from typing import Protocol

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.schemas import LLMSmokeResponse, PlanningRequest
from app.schemas.domain import BudgetSummary, MealItem, ShoppingItem, TaskItem
from app.services.demo_data import MEALS, SHOPPING, TASKS

TokenSink = Callable[[str], Awaitable[None]]
token_sink: ContextVar[TokenSink | None] = ContextVar("solochef_token_sink", default=None)


class LLMGenerationError(RuntimeError):
    """Raised when the configured model cannot return a valid plan."""


class PlanDraft(BaseModel):
    summary: str
    meals: list[MealItem]
    shopping: list[ShoppingItem]
    tasks: list[TaskItem]
    budget: BudgetSummary
    conflicts: list[str]
    suggestions: list[str]


class PlanGenerator(Protocol):
    @property
    def mode(self) -> str: ...

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft: ...


class DemoPlanGenerator:
    @property
    def mode(self) -> str:
        return "demo"

    async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
        del context
        estimated = min(472.0, request.budget * 0.94)
        return PlanDraft(
            summary="已结合营养目标、预算约束和检索上下文生成一周膳食计划。",
            meals=MEALS,
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
            suggestions=["周三安排 18 分钟快手餐", "复用菌菇与青菜以减少浪费"],
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
            "解释文字或 Schema 之外的字段。meals 必须恰好包含周一至周日 7 项；"
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
            return PlanDraft.model_validate(json.loads(content))
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

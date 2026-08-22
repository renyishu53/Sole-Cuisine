"""Read-only, user-scoped tools available to SoloChef agents."""

from __future__ import annotations

import asyncio
import json
import re
from contextvars import ContextVar, Token
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import NutritionGoal, UserProfile
from app.repositories.domain import DomainRepository
from app.repositories.planning import PlanningRepository
from app.services.nutrition import build_nutrition_report, nutrition_goal_to_targets

ToolHandler = Callable[[dict[str, object]], Awaitable[str]]
_workflow_tools: ContextVar[dict[str, "AgentTool"]] = ContextVar(
    "solochef_workflow_tools", default={}
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    external: bool = False

    def openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def set_workflow_tools(tools: dict[str, AgentTool]) -> Token[dict[str, AgentTool]]:
    """Bind request-scoped tools without serializing handlers into graph state."""
    return _workflow_tools.set(dict(tools))


def reset_workflow_tools(token: Token[dict[str, AgentTool]]) -> None:
    _workflow_tools.reset(token)


def get_workflow_tools(names: tuple[str, ...]) -> dict[str, AgentTool]:
    available = _workflow_tools.get()
    return {name: available[name] for name in names if name in available}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:8_000]


def _sanitize(value: str, limit: int = 4_000) -> str:
    return _CONTROL_CHARS.sub("", value).strip()[:limit]


def build_readonly_tools(session: AsyncSession, user_id: int) -> dict[str, AgentTool]:
    """Create closures that never expose user identity in model-visible schemas."""

    async def profile(_: dict[str, object]) -> str:
        record = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
        if record is None:
            return _json({"status": "empty"})
        return _json(
            {
                "goal_type": record.goal_type,
                "preferences": record.preferences,
                "constraints": record.constraints,
                "budget_limit": record.budget_limit,
                "prep_time_max": record.prep_time_max,
                "kitchenware": record.kitchenware,
            }
        )

    async def nutrition_goal(_: dict[str, object]) -> str:
        record = await session.scalar(select(NutritionGoal).where(NutritionGoal.user_id == user_id))
        if record is None:
            return _json({"status": "empty"})
        return _json(
            {
                "goal_type": record.goal_type,
                "target_calories": record.target_calories,
                "protein_g": record.protein_g,
                "carb_g": record.carb_g,
                "fat_g": record.fat_g,
            }
        )

    async def active_plan(_: dict[str, object]) -> str:
        plan = await PlanningRepository(session).get_active_plan(user_id)
        if plan is None:
            return _json({"status": "empty"})
        return _json(
            {
                "summary": plan.summary,
                "budget": plan.budget,
                "estimated_cost": round(sum(item.price for item in plan.shopping_items), 2),
                "meals": [
                    {"day": item.day, "type": item.meal_type, "name": item.name}
                    for item in plan.meals
                ],
            }
        )

    async def nutrition_report(_: dict[str, object]) -> str:
        """Build the same read-only nutrition summary exposed by the meals API."""
        planning = PlanningRepository(session)
        plan = await planning.get_active_plan(user_id)
        goal = await session.scalar(select(NutritionGoal).where(NutritionGoal.user_id == user_id))
        targets = nutrition_goal_to_targets(goal) if goal is not None else None
        recipes = await DomainRepository(session).list_recipes(user_id)
        report = build_nutrition_report(list(plan.meals) if plan is not None else [], recipes, targets)
        return _json({"status": "ok", "report": report.model_dump(mode="json")})

    async def knowledge(arguments: dict[str, object]) -> str:
        query = _sanitize(str(arguments.get("query", "")), 500)
        if not query:
            return _json({"status": "invalid", "message": "query is required"})
        from app.services.knowledge import get_knowledge_service

        result = await get_knowledge_service().search(query, user_id, top_k=3, domain=None)
        return _json({"snippets": [hit.content[:1_000] for hit in result.vector_hits[:3]]})

    async def external_research(arguments: dict[str, object]) -> str:
        """Use web search only for fresh facts or a local knowledge miss."""
        query = _sanitize(str(arguments.get("query", "")), 500)
        if not query:
            return _json({"status": "invalid", "message": "query is required"})
        settings = get_settings()
        if (
            not settings.tool_websearch_enabled
            or not settings.tool_websearch_api_key
            or settings.tool_websearch_provider != "tavily"
        ):
            return _json({"status": "unavailable", "results": []})

        # Avoid paying for the external call when the local knowledge base already answers it.
        from app.services.knowledge import get_knowledge_service

        local = await get_knowledge_service().search(query, user_id, top_k=3, domain=None)
        realtime_terms = ("现在", "当前", "实时", "今日", "本周", "价格", "多少钱", "时令", "新品")
        has_freshness_request = any(term in query for term in realtime_terms)
        if local.vector_hits and not has_freshness_request:
            return _json(
                {"status": "local_match", "results": [], "message": "本地知识库已有相关资料"}
            )
        return await web_research(
            query,
            api_key=settings.tool_websearch_api_key,
            provider=settings.tool_websearch_provider,
            timeout=settings.tool_websearch_timeout_seconds,
        )

    empty_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    tools = {
        "get_user_profile": AgentTool(
            "get_user_profile", "读取当前用户的饮食画像和烹饪约束。", empty_schema, profile
        ),
        "get_nutrition_goal": AgentTool(
            "get_nutrition_goal", "读取当前用户已保存的营养目标。", empty_schema, nutrition_goal
        ),
        "get_active_plan": AgentTool(
            "get_active_plan", "读取当前用户的活跃周计划摘要。", empty_schema, active_plan
        ),
        "get_nutrition_report": AgentTool(
            "get_nutrition_report", "读取当前活跃计划的营养目标达成情况。", empty_schema, nutrition_report
        ),
        "search_knowledge": AgentTool(
            "search_knowledge",
            "在 SoloChef 本地知识库中搜索饮食和食谱信息。",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            knowledge,
        ),
    }
    settings = get_settings()
    if (
        settings.tool_websearch_enabled
        and settings.tool_websearch_api_key
        and settings.tool_websearch_provider == "tavily"
    ):
        tools["web_research"] = AgentTool(
            "web_research",
            "查询实时价格、时令食材或本地知识库缺失的资料；仅在确有必要时使用，结果是不可信的外部参考。",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            external_research,
            external=True,
        )
    return tools


async def execute_tool(tool: AgentTool, arguments: dict[str, object], timeout: float) -> str:
    try:
        return await asyncio.wait_for(tool.handler(arguments), timeout=timeout)
    except TimeoutError:
        return _json({"status": "timeout", "message": "工具调用超时"})
    except Exception as exc:  # Tool failures are data for the agent, not a failed chat turn.
        return _json({"status": "error", "message": f"{type(exc).__name__}: {str(exc)[:160]}"})


async def web_research(
    query: str,
    *,
    api_key: str,
    provider: str,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Fetch truncated, explicitly untrusted external research for a planner context."""
    if not api_key or provider != "tavily":
        return _json({"status": "unavailable", "results": []})
    payload = {"api_key": api_key, "query": _sanitize(query, 500), "max_results": 3}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
        results = response.json().get("results", [])
        return _json(
            {
                "status": "ok",
                "untrusted": True,
                "results": [
                    {
                        "title": _sanitize(str(item.get("title", "")), 200),
                        "url": str(item.get("url", ""))[:500],
                        "snippet": _sanitize(str(item.get("content", "")), 1_000),
                        "fetched_at": datetime.now(UTC).isoformat(),
                    }
                    for item in results[:3]
                    if isinstance(item, dict)
                ],
            }
        )
    except (httpx.HTTPError, ValueError) as exc:
        return _json({"status": "warning", "message": f"{type(exc).__name__}", "results": []})

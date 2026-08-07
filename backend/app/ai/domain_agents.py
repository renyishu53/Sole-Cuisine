import json
from collections.abc import Mapping, Sequence
from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.ai.prompts import PromptVersion, get_active
from app.core.config import Settings
from app.schemas import CalendarEvent, MemberProfile, PlanningRequest
from app.schemas.domain import (
    BudgetAgentResult,
    MealAgentResult,
    ShoppingAgentResult,
    TaskAgentResult,
    TaskAssignmentCandidate,
)

ResultT = TypeVar("ResultT", bound=BaseModel)


def _as_sequence(value: object) -> Sequence[object]:
    """把口味画像里的字段安全地取成序列（字符串不拆成字符）。"""
    if isinstance(value, str) or value is None:
        return ()
    return tuple(value) if isinstance(value, (list, tuple, set)) else ()


def _meal_strategy(liked: Sequence[str], disliked: Sequence[str]) -> str:
    """确定性回退时也要如实说明"学到了什么"，便于前端展示与审计。"""
    base = "按成员硬约束过滤，再优先快手、可复用食材和日常偏好"
    if not liked and not disliked:
        return base
    learned: list[str] = []
    if liked:
        learned.append(f"历史反馈偏好 {'、'.join(liked[:4])}")
    if disliked:
        learned.append(f"回避 {'、'.join(disliked[:4])}")
    return f"{base}；结合{'，'.join(learned)}"


class StructuredDomainAgentEngine:
    """Runs small schema-bound domain calls with deterministic local fallbacks.

    提示词与系统角色由 :mod:`app.ai.prompts` 版本注册表统一管理，每个智能体
    运行时会记录所用的提示词版本，便于评测与审计。
    """

    def __init__(self, settings: Settings, *, use_llm: bool) -> None:
        self._model: ChatOpenAI | None = None
        if use_llm and settings.real_llm_enabled:
            self._model = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                temperature=0.1,
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
                max_tokens=900,
            )

    async def meal(
        self,
        request: PlanningRequest,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
        taste_profile: Mapping[str, object] | None = None,
    ) -> tuple[MealAgentResult, str, str]:
        """规划餐食筛选策略。

        ``taste_profile`` 由 :meth:`app.repositories.FeedbackRepository.taste_profile`
        从历史执行反馈聚合而来。它同时作用于两条路径：LLM 路径写进提示词输入，
        确定性回退路径直接参与标签排序与排除项计算——保证没有 LLM 时反馈依然被学习。
        """
        constraints = sorted({item for member in members for item in member.constraints})
        preferences = sorted({item for member in members for item in member.preferences})
        profile = taste_profile or {}
        liked = [str(tag) for tag in _as_sequence(profile.get("liked_tags"))]
        disliked = [str(tag) for tag in _as_sequence(profile.get("disliked_tags"))]
        rejected = [str(name) for name in _as_sequence(profile.get("rejected_dishes"))]
        # 反馈学到的偏好排在成员静态偏好之前，负向标签一律剔除
        merged_tags = [
            tag for tag in (*liked, *preferences) if tag not in disliked
        ]
        fallback = MealAgentResult(
            strategy=_meal_strategy(liked, disliked),
            constraints_applied=constraints,
            excluded_ingredients=sorted({*constraints, *disliked, *rejected}),
            preferred_tags=list(dict.fromkeys(merged_tags)) or ["日常友好", "营养均衡"],
            max_duration_minutes=25 if "快手" in request.prompt or "快手" in liked else 40,
        )
        return await self._generate(
            MealAgentResult,
            get_active("meal"),
            request,
            members,
            events,
            fallback,
            extra_payload={"taste_profile": dict(profile)} if profile else None,
        )

    async def shopping(
        self,
        request: PlanningRequest,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
    ) -> tuple[ShoppingAgentResult, str, str]:
        fallback = ShoppingAgentResult(
            strategy="按标准化食材名和分类合并，同类数量保留可追溯来源",
            merge_keys=["name", "category"],
            preferred_categories=["蔬菜", "肉蛋奶", "主食", "调味品"],
            purchase_windows=["周中补货", "周末集中采购"],
        )
        return await self._generate(
            ShoppingAgentResult,
            get_active("shopping"),
            request,
            members,
            events,
            fallback,
        )

    async def task(
        self,
        request: PlanningRequest,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
    ) -> tuple[TaskAgentResult, str, str]:
        candidates = [
            TaskAssignmentCandidate(
                member_id=member.id,
                member_name=member.name,
                availability=member.availability,
                priority=index + 1,
            )
            for index, member in enumerate(members)
        ]
        fallback = TaskAgentResult(
            strategy="按当前任务负担从低到高分配，并避开成员真实日程",
            fairness_rule="任务数量优先，其次累计时长，儿童只分配适龄低风险任务",
            candidates=candidates,
            default_duration_minutes=20,
        )
        return await self._generate(
            TaskAgentResult,
            get_active("task"),
            request,
            members,
            events,
            fallback,
        )

    async def budget(
        self,
        request: PlanningRequest,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
    ) -> tuple[BudgetAgentResult, str, str]:
        limit = request.budget
        fallback = BudgetAgentResult(
            strategy="预留 10% 弹性金额，分类限额之和不超过总预算",
            limit=limit,
            reserve=round(limit * 0.1, 2),
            warning_threshold_percent=85,
            category_limits={
                "肉蛋奶": round(limit * 0.38, 2),
                "蔬菜": round(limit * 0.24, 2),
                "主食": round(limit * 0.15, 2),
                "其他": round(limit * 0.13, 2),
            },
        )
        return await self._generate(
            BudgetAgentResult,
            get_active("budget"),
            request,
            members,
            events,
            fallback,
        )

    async def _generate(
        self,
        schema: type[ResultT],
        prompt: PromptVersion,
        request: PlanningRequest,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
        fallback: ResultT,
        extra_payload: Mapping[str, object] | None = None,
    ) -> tuple[ResultT, str, str]:
        """运行领域智能体，返回 (结果, 模式, 错误说明)。

        模式串编码提示词版本信息：deterministic / llm:v{version} /
        deterministic-fallback:v{version}，便于在评测和审计中追溯所用提示词版本。
        ``extra_payload`` 用于注入领域特有上下文（如餐食的口味画像）。
        """
        if self._model is None:
            return fallback, f"deterministic:v{prompt.version}", ""
        payload: dict[str, object] = {
            "request": request.model_dump(),
            "members": [member.model_dump() for member in members],
            "events": [event.model_dump(mode="json") for event in events[:30]],
        }
        if extra_payload:
            payload.update(extra_payload)
        user_prompt = (
            f"{prompt.instruction}\n只输出 JSON，不得添加解释。\n"
            f"Schema: {json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            response = await self._model.bind(response_format={"type": "json_object"}).ainvoke(
                [("system", prompt.system_message), ("user", user_prompt)]
            )
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            return schema.model_validate_json(content), f"llm:v{prompt.version}", ""
        except Exception as exc:
            return (
                fallback,
                f"deterministic-fallback:v{prompt.version}",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )

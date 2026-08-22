import json
from collections.abc import Mapping, Sequence
from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.ai.agent_tools import execute_tool, get_workflow_tools
from app.ai.prompts import PromptVersion, get_active
from app.core.config import Settings
from app.schemas import PlanningRequest
from app.schemas.domain import (
    BudgetAgentResult,
    BudgetSelfCheck,
    MealAgentResult,
    ShoppingAgentResult,
)


def reconcile_budget(result: BudgetAgentResult, budget_limit: float) -> BudgetAgentResult:
    """确定性兜底：确保 分类限额之和 + 预留金额 == 周预算。

    如果 AI 生成的结果不满足等式，自动按比例压缩/扩展各分类限额，
    使等式成立。这样前端永远收不到"分类限额超预算"的硬冲突。
    """
    category_sum = sum(result.category_limits.values())
    total_check = category_sum + result.reserve
    expected = budget_limit

    if abs(total_check - expected) < 0.01:
        # 已经自洽，只填充 self_check
        result.self_check = BudgetSelfCheck(
            category_sum=round(category_sum, 2),
            total_check=round(total_check, 2),
            expected=expected,
            matched=True,
        )
        return result

    # 不满足等式，按比例调整分类限额
    allocatable = max(0, expected - result.reserve)
    if category_sum > 0:
        scale = allocatable / category_sum
        result.category_limits = {
            k: round(v * scale, 2) for k, v in result.category_limits.items()
        }
    else:
        # 分类限额全为 0，等额分配
        n = len(result.category_limits) or 1
        each = round(allocatable / n, 2)
        result.category_limits = {k: each for k in result.category_limits}

    # 重新计算并填充 self_check
    new_category_sum = sum(result.category_limits.values())
    result.self_check = BudgetSelfCheck(
        category_sum=round(new_category_sum, 2),
        total_check=round(new_category_sum + result.reserve, 2),
        expected=expected,
        matched=abs(new_category_sum + result.reserve - expected) < 0.01,
    )
    return result


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
        self._settings = settings
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
        taste_profile: Mapping[str, object] | None = None,
        constraints: Sequence[str] = (),
        preferences: Sequence[str] = (),
        prep_time_max: int | None = None,
        kitchenware: Sequence[str] = (),
        directive: Mapping[str, object] | None = None,
    ) -> tuple[MealAgentResult, str, str]:
        """规划餐食筛选策略。

        SoloChef 单人场景下，忌口/偏好约束来自 ``UserProfile.constraints`` /
        ``preferences``（经 workflow state 注入，优先取用），``constraints`` /
        ``preferences`` 缺省时回退到空约束。

        生活约束（阶段1）：``prep_time_max`` 直接决定 ``max_duration_minutes``，
        优先于"快手"启发式；``kitchenware`` 进入硬约束，供规划器排除需要清单之外
        厨具的菜式。

        ``taste_profile`` 由 :meth:`app.repositories.FeedbackRepository.taste_profile`
        从历史执行反馈聚合而来。它同时作用于两条路径：LLM 路径写进提示词输入，
        确定性回退路径直接参与标签排序与排除项计算——保证没有 LLM 时反馈依然被学习。
        """
        hard_constraints = sorted(set(constraints))
        user_preferences = sorted(set(preferences))
        available_tools = sorted(set(kitchenware))
        profile = taste_profile or {}
        liked = [str(tag) for tag in _as_sequence(profile.get("liked_tags"))]
        disliked = [str(tag) for tag in _as_sequence(profile.get("disliked_tags"))]
        rejected = [str(name) for name in _as_sequence(profile.get("rejected_dishes"))]
        # 反馈学到的偏好排在单人画像静态偏好之前，负向标签一律剔除
        merged_tags = [
            tag for tag in (*liked, *user_preferences) if tag not in disliked
        ]

        # 生活约束：备餐时间上限优先于"快手"启发式；厨具清单进入硬约束
        if prep_time_max is not None:
            max_duration = max(5, min(240, prep_time_max))
        else:
            max_duration = 25 if "快手" in request.prompt or "快手" in liked else 40
        life_constraints: list[str] = []
        if available_tools:
            life_constraints.append(f"仅使用厨具：{'、'.join(available_tools)}")

        fallback = MealAgentResult(
            strategy=_meal_strategy(liked, disliked),
            constraints_applied=[*hard_constraints, *life_constraints],
            excluded_ingredients=sorted({*hard_constraints, *disliked, *rejected}),
            preferred_tags=list(dict.fromkeys(merged_tags)) or ["日常友好", "营养均衡"],
            max_duration_minutes=max_duration,
        )

        extra_payload: dict[str, object] = {}
        if profile:
            extra_payload["taste_profile"] = dict(profile)
        if available_tools or prep_time_max is not None:
            extra_payload["lifestyle"] = {
                "prep_time_max_minutes": prep_time_max,
                "kitchenware": available_tools,
            }
        if directive:
            extra_payload["supervisor_directive"] = dict(directive)
        return await self._generate(
            MealAgentResult,
            get_active("meal"),
            request,
            fallback,
            extra_payload=extra_payload or None,
            tool_names=(
                "search_knowledge",
                "get_user_profile",
                "get_nutrition_goal",
                "get_nutrition_report",
            ),
        )

    async def shopping(
        self,
        request: PlanningRequest,
        directive: Mapping[str, object] | None = None,
    ) -> tuple[ShoppingAgentResult, str, str]:
        fallback = ShoppingAgentResult(
            strategy="按标准化食材名和分类合并，同类数量保留可追溯来源",
            merge_keys=["name", "category"],
            preferred_categories=["蔬菜", "肉蛋奶", "主食", "调味品"],
            purchase_windows=["按需采购"],
        )
        return await self._generate(
            ShoppingAgentResult,
            get_active("shopping"),
            request,
            fallback,
            extra_payload={"supervisor_directive": dict(directive)} if directive else None,
            tool_names=("search_knowledge", "get_active_plan", "web_research"),
        )

    async def budget(
        self,
        request: PlanningRequest,
        directive: Mapping[str, object] | None = None,
    ) -> tuple[BudgetAgentResult, str, str]:
        limit = request.budget
        # 确定性回退：预留 10%，分类限额之和严格等于 limit - reserve
        reserve = round(limit * 0.1, 2)
        allocatable = round(limit - reserve, 2)
        fallback = BudgetAgentResult(
            strategy="预留 10% 弹性金额，分类限额之和严格等于可分配总额",
            limit=limit,
            reserve=reserve,
            warning_threshold_percent=85,
            category_limits={
                "肉蛋奶": round(allocatable * 0.42, 2),
                "蔬菜": round(allocatable * 0.26, 2),
                "主食": round(allocatable * 0.17, 2),
                "其他": round(allocatable - round(allocatable * 0.42, 2) - round(allocatable * 0.26, 2) - round(allocatable * 0.17, 2), 2),
            },
        )
        result, mode, error = await self._generate(
            BudgetAgentResult,
            get_active("budget"),
            request,
            fallback,
            extra_payload={"supervisor_directive": dict(directive)} if directive else None,
            tool_names=("search_knowledge", "get_active_plan", "get_nutrition_goal"),
        )
        # 第二层防御：确定性兜底，确保分类之和 + 预留 == 周预算
        result = reconcile_budget(result, limit)
        return result, mode, error

    async def _generate(
        self,
        schema: type[ResultT],
        prompt: PromptVersion,
        request: PlanningRequest,
        fallback: ResultT,
        extra_payload: Mapping[str, object] | None = None,
        tool_names: tuple[str, ...] = (),
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
        }
        if extra_payload:
            payload.update(extra_payload)
        user_prompt = (
            f"{prompt.instruction}\n只输出 JSON，不得添加解释。\n"
            f"Schema: {json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            messages: list[object] = [("system", prompt.system_message), ("user", user_prompt)]
            tools = get_workflow_tools(tool_names)
            bound_model = (
                self._model.bind_tools([tool.openai_schema() for tool in tools.values()])
                if tools
                else self._model.bind(response_format={"type": "json_object"})
            )
            tool_calls_used = 0
            tool_names_used: list[str] = []
            for _ in range(self._settings.domain_agent_max_iterations):
                response = await bound_model.ainvoke(messages)
                tool_calls = getattr(response, "tool_calls", []) or []
                if not tool_calls:
                    content = response.content if isinstance(response.content, str) else str(response.content)
                    mode = (
                        f"llm-react:v{prompt.version}:{tool_calls_used}tools:{','.join(dict.fromkeys(tool_names_used))}"
                        if tools else f"llm:v{prompt.version}"
                    )
                    return schema.model_validate_json(content), mode, ""
                messages.append(response)
                for call in tool_calls:
                    if tool_calls_used >= self._settings.domain_agent_max_tool_calls:
                        break
                    name = str(call.get("name", ""))
                    arguments = call.get("args", {})
                    safe_arguments = arguments if isinstance(arguments, dict) else {}
                    tool = tools.get(name)
                    if tool is None:
                        result = json.dumps({"status": "invalid", "message": "未注册工具"}, ensure_ascii=False)
                    else:
                        result = await execute_tool(
                            tool, safe_arguments, self._settings.domain_agent_tool_timeout_seconds
                        )
                    tool_calls_used += 1
                    tool_names_used.append(name or "unknown")
                    from langchain_core.messages import ToolMessage

                    messages.append(
                        ToolMessage(content=result, tool_call_id=str(call.get("id", "")))
                    )
            return fallback, f"deterministic-fallback:v{prompt.version}", "领域工具调用达到上限"
        except Exception as exc:
            return (
                fallback,
                f"deterministic-fallback:v{prompt.version}",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )

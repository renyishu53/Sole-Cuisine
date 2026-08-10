import json
import operator
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Protocol, TypedDict
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.ai.domain_agents import StructuredDomainAgentEngine
from app.ai.llm import DemoPlanGenerator, LLMGenerationError, PlanDraft, PlanGenerator
from app.core.config import Settings, get_settings
from app.schemas import (
    AgentRun,
    CalendarEvent,
    GraphSearchHit,
    MemberProfile,
    PlanningRequest,
    PlanningResponse,
    VectorSearchHit,
)
from app.schemas.domain import (
    AgentStatus,
    AgentStep,
    BudgetAgentResult,
    DomainAgentBundle,
    MealAgentResult,
    ShoppingAgentResult,
)
from app.services.knowledge import get_knowledge_service
from app.services.nutrition import estimate_meal_nutrition


class WorkflowState(TypedDict, total=False):
    request: PlanningRequest
    members: list[MemberProfile]
    events: list[CalendarEvent]
    #: 单人用户画像的忌口/过敏约束（来自 UserProfile.constraints），SoloChef
    #: 忌口校验的优先数据源；members 仅为家庭时期遗留兼容回退
    user_constraints: list[str]
    #: 单人用户画像的饮食偏好（来自 UserProfile.preferences）
    user_preferences: list[str]
    #: 历史执行反馈聚合出的口味画像，餐食智能体据此做偏好学习
    taste_profile: dict[str, object]
    #: 营养目标（TDEE + 宏量分配），由 planning_service 从 DB 加载后注入
    nutrition_targets: dict[str, float]
    intent: dict[str, object]
    graph_hits: list[GraphSearchHit]
    vector_hits: list[VectorSearchHit]
    graph_status: str
    vector_status: str
    context: str
    draft: PlanDraft
    llm_mode: str
    validation_warnings: list[str]
    domain_results: Annotated[list[dict[str, object]], operator.add]
    domain_bundle: DomainAgentBundle
    domain_context: str
    specialist_outputs: Annotated[list[dict[str, object]], operator.add]
    sources: list[str]
    trace: Annotated[list[AgentStep], operator.add]


class KnowledgeRetriever(Protocol):
    async def retrieve_graph(
        self,
        query: str,
        user_id: int,
        members: Sequence[MemberProfile] = (),
        events: Sequence[CalendarEvent] = (),
    ) -> tuple[list[GraphSearchHit], str]: ...

    async def retrieve_vector(
        self, query: str, user_id: int, top_k: int
    ) -> tuple[list[VectorSearchHit], str, str]: ...


class SoloChefWorkflow:
    """Graph RAG planner for SoloChef, implemented as an executable LangGraph graph."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        knowledge: KnowledgeRetriever | None = None,
        generator: PlanGenerator | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._knowledge = knowledge or get_knowledge_service()
        if generator is None:
            from app.ai.llm import build_plan_generator

            generator = build_plan_generator(self._settings)
        self._generator = generator
        self._domain_engine = StructuredDomainAgentEngine(
            self._settings,
            use_llm=self._generator.mode != "demo",
        )
        self._checkpointer: BaseCheckpointSaver[str] | None = None
        self._graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(WorkflowState)
        builder.add_node("intent", self._intent_node)
        builder.add_node("graph_retriever", self._graph_retriever_node)
        builder.add_node("vector_retriever", self._vector_retriever_node)
        builder.add_node("coordinator", self._coordinator_node)
        builder.add_node("meal_agent", self._meal_agent_node)
        builder.add_node("shopping_agent", self._shopping_agent_node)
        builder.add_node("budget_agent", self._budget_agent_node)
        builder.add_node("domain_coordinator", self._domain_coordinator_node)
        builder.add_node("planner", self._planner_node)
        builder.add_node("verifier", self._verifier_node)
        builder.add_node("final_planner", self._final_node)
        builder.add_edge(START, "intent")
        builder.add_edge("intent", "graph_retriever")
        builder.add_edge("intent", "vector_retriever")
        builder.add_edge(["graph_retriever", "vector_retriever"], "coordinator")
        builder.add_edge("coordinator", "meal_agent")
        builder.add_edge("coordinator", "shopping_agent")
        builder.add_edge("coordinator", "budget_agent")
        builder.add_edge(
            ["meal_agent", "shopping_agent", "budget_agent"],
            "domain_coordinator",
        )
        builder.add_edge("domain_coordinator", "planner")
        builder.add_edge("planner", "verifier")
        builder.add_edge("verifier", "final_planner")
        builder.add_edge("final_planner", END)
        return builder.compile(checkpointer=self._checkpointer)

    def set_checkpointer(self, checkpointer: BaseCheckpointSaver[str] | None) -> None:
        if checkpointer is self._checkpointer:
            return
        self._checkpointer = checkpointer
        self._graph = self._build_graph()

    async def run(
        self,
        request: PlanningRequest | None,
        members: Sequence[MemberProfile] = (),
        events: Sequence[CalendarEvent] = (),
        run_id: UUID | None = None,
        on_step: Callable[[AgentStep], Awaitable[None]] | None = None,
        resume: bool = False,
        taste_profile: dict[str, object] | None = None,
        nutrition_targets: dict[str, float] | None = None,
        user_constraints: Sequence[str] = (),
        user_preferences: Sequence[str] = (),
    ) -> PlanningResponse:
        state: WorkflowState = {}
        config: RunnableConfig | None = None
        if self._checkpointer is not None and run_id is not None:
            config = {"configurable": {"thread_id": str(run_id)}}
        seen_steps = 0
        graph_input: WorkflowState | None
        if resume:
            if config is None:
                raise RuntimeError("LangGraph checkpoint is unavailable")
            snapshot = await self._graph.aget_state(config)
            if not snapshot.values:
                raise RuntimeError("LangGraph checkpoint does not exist")
            seen_steps = len(snapshot.values.get("trace", []))
            graph_input = None
        else:
            if request is None:
                raise ValueError("request is required for a new workflow")
            graph_input = {
                "request": request,
                "members": list(members),
                "events": list(events),
                "taste_profile": dict(taste_profile or {}),
                "nutrition_targets": dict(nutrition_targets or {}),
                "user_constraints": list(user_constraints),
                "user_preferences": list(user_preferences),
                "trace": [],
            }
        async for current in self._graph.astream(
            graph_input,
            config=config,
            stream_mode="values",
            durability="sync" if config is not None else None,
        ):
            state = current
            trace = state.get("trace", [])
            if on_step is not None:
                for step in trace[seen_steps:]:
                    await on_step(step)
            seen_steps = len(trace)
        draft = state["draft"]
        return PlanningResponse(
            run_id=run_id or uuid4(),
            summary=draft.summary,
            meals=draft.meals,
            shopping=draft.shopping,
            tasks=draft.tasks,
            budget=draft.budget,
            conflicts=draft.conflicts,
            suggestions=draft.suggestions,
            domain=state["domain_bundle"],
            sources=state["sources"],
            trace=state["trace"],
        )

    async def _intent_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        intent = {
            "type": "weekly_plan",
            "user_id": request.user_id,
            "budget": request.budget,
            "requires": ["meals", "shopping", "budget"],
        }
        return {
            "intent": intent,
            "trace": [
                self._step(start, "intent", "Intent Agent", "识别规划意图与硬约束", intent)
            ],
        }

    async def _graph_retriever_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        hits, status = await self._knowledge.retrieve_graph(
            request.prompt,
            request.user_id,
            state.get("members", []),
            state.get("events", []),
        )
        output: dict[str, object] = {
            "status": status,
            "relations": [hit.model_dump() for hit in hits],
        }
        return {
            "graph_hits": hits,
            "graph_status": status,
            "trace": [
                self._step(
                    start,
                    "graph_retriever",
                    "Graph Retriever",
                    f"从 Neo4j 召回 {len(hits)} 条成员、偏好与日程关系",
                    output,
                    AgentStatus.COMPLETED if status == "connected" else AgentStatus.WARNING,
                )
            ],
        }

    async def _vector_retriever_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        hits, status, _rerank_status = await self._knowledge.retrieve_vector(
            request.prompt, request.user_id, self._settings.rag_top_k
        )
        output: dict[str, object] = {
            "status": status,
            "top_k": self._settings.rag_top_k,
            "chunks": [hit.model_dump() for hit in hits],
        }
        return {
            "vector_hits": hits,
            "vector_status": status,
            "trace": [
                self._step(
                    start,
                    "vector_retriever",
                    "Vector Retriever",
                    f"从 Chroma 召回 {len(hits)} 个语义相关知识片段",
                    output,
                    AgentStatus.COMPLETED if status == "connected" else AgentStatus.WARNING,
                )
            ],
        }

    async def _coordinator_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        graph_lines = [
            f"- {hit.subject} --{hit.relation}--> {hit.target} ({hit.detail})"
            for hit in state.get("graph_hits", [])
        ]
        vector_lines = [
            f"- [{hit.document_name}#{hit.chunk_index}, score={hit.score}] {hit.content}"
            for hit in state.get("vector_hits", [])
        ]
        context = "知识图谱：\n" + ("\n".join(graph_lines) or "- 无可用图谱关系")
        context += "\n\n向量知识片段：\n" + ("\n".join(vector_lines) or "- 无可用向量片段")
        output: dict[str, object] = {
            "graph_relations": len(graph_lines),
            "vector_chunks": len(vector_lines),
            "context_chars": len(context),
        }
        return {
            "context": context,
            "trace": [
                self._step(
                    start,
                    "coordinator",
                    "Coordinator Agent",
                    "融合图谱硬约束与向量语义上下文",
                    output,
                )
            ],
        }

    async def _planner_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        mode = self._generator.mode
        status = AgentStatus.COMPLETED
        fallback_reason = ""
        specialist_context = "\n\n领域专家建议：\n" + "\n".join(
            f"- {item['agent']}: {item['recommendation']}"
            for item in state.get("specialist_outputs", [])
        )
        specialist_context += "\n\n结构化领域约束：\n" + state.get("domain_context", "")
        try:
            draft = await self._generator.generate(request, state["context"] + specialist_context)
        except LLMGenerationError as exc:
            if not self._settings.ai_fallback_enabled:
                raise
            draft = await DemoPlanGenerator().generate(
                request, state["context"] + specialist_context
            )
            mode = f"{mode}->demo-fallback"
            status = AgentStatus.WARNING
            fallback_reason = str(exc)
        output: dict[str, object] = {
            "llm_mode": mode,
            "meals": len(draft.meals),
            "shopping_items": len(draft.shopping),
            "tasks": len(draft.tasks),
            "specialists": [item["agent"] for item in state.get("specialist_outputs", [])],
        }
        if fallback_reason:
            output["fallback_reason"] = fallback_reason
        return {
            "draft": draft,
            "llm_mode": mode,
            "trace": [
                self._step(
                    start,
                    "planner",
                    "Planning Agent",
                    "基于融合上下文生成结构化菜单、采购与预算计划",
                    output,
                    status,
                )
            ],
        }

    async def _meal_agent_node(self, state: WorkflowState) -> dict[str, object]:
        result, mode, error = await self._domain_engine.meal(
            state["request"],
            state.get("members", []),
            state.get("events", []),
            state.get("taste_profile"),
            constraints=state.get("user_constraints", []),
            preferences=state.get("user_preferences", []),
        )
        return self._structured_specialist_result(
            "meal_agent",
            "Meal Agent",
            "产出餐食硬约束、排除食材和烹饪时长结构结果",
            result,
            mode,
            error,
        )

    async def _shopping_agent_node(self, state: WorkflowState) -> dict[str, object]:
        result, mode, error = await self._domain_engine.shopping(
            state["request"], state.get("members", []), state.get("events", [])
        )
        return self._structured_specialist_result(
            "shopping_agent",
            "Shopping Agent",
            "产出采购合并键、分类和采购批次结构结果",
            result,
            mode,
            error,
        )

    async def _budget_agent_node(self, state: WorkflowState) -> dict[str, object]:
        result, mode, error = await self._domain_engine.budget(
            state["request"], state.get("members", []), state.get("events", [])
        )
        return self._structured_specialist_result(
            "budget_agent",
            "Budget Agent",
            "产出分类限额、预留金额和预警阈值结构结果",
            result,
            mode,
            error,
        )

    @staticmethod
    def _structured_specialist_result(
        name: str,
        label: str,
        summary: str,
        result: BaseModel,
        mode: str,
        error: str,
    ) -> dict[str, object]:
        start = perf_counter()
        payload = result.model_dump(mode="json")
        output: dict[str, object] = {"mode": mode, "result": payload}
        if error:
            output["fallback_reason"] = error
        return {
            "domain_results": [{"kind": name.removesuffix("_agent"), "result": payload}],
            "specialist_outputs": [{"agent": label, "recommendation": result.model_dump_json()}],
            "trace": [
                SoloChefWorkflow._step(
                    start,
                    name,
                    label,
                    summary,
                    output,
                    AgentStatus.WARNING if error else AgentStatus.COMPLETED,
                )
            ],
        }

    async def _domain_coordinator_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        result_by_kind = {
            str(item["kind"]): item["result"] for item in state.get("domain_results", [])
        }
        meal = MealAgentResult.model_validate(result_by_kind["meal"])
        shopping = ShoppingAgentResult.model_validate(result_by_kind["shopping"])
        budget = BudgetAgentResult.model_validate(result_by_kind["budget"])
        merged_constraints = list(
            dict.fromkeys(
                [
                    *meal.constraints_applied,
                    f"单餐时长不超过 {meal.max_duration_minutes} 分钟",
                    f"预算预留 {budget.reserve:.2f} 元",
                    shopping.strategy,
                ]
            )
        )
        bundle = DomainAgentBundle(
            meal=meal,
            shopping=shopping,
            budget=budget,
            merged_constraints=merged_constraints,
        )
        payload = bundle.model_dump(mode="json")
        return {
            "domain_bundle": bundle,
            "domain_context": json.dumps(payload, ensure_ascii=False),
            "trace": [
                self._step(
                    start,
                    "domain_coordinator",
                    "Domain Coordinator",
                    "校验并合并三个领域 Agent 的结构化中间结果",
                    payload,
                )
            ],
        }

    async def _verifier_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        draft = state["draft"]
        warnings: list[str] = []
        domain = state["domain_bundle"]
        if draft.budget.estimated > request.budget:
            warnings.append("模型估算超过预算上限，已将预算摘要限制到上限")
            draft.budget.estimated = request.budget
        draft.budget.limit = request.budget
        draft.budget.saved = max(0, request.budget - draft.budget.estimated)
        draft.budget.usage_percent = round(draft.budget.estimated / request.budget * 100)

        constraints = [
            hit.target for hit in state.get("graph_hits", []) if hit.relation == "HAS_CONSTRAINT"
        ]
        # SoloChef 单人画像忌口约束（来自 UserProfile.constraints，优先取用）；
        # members 仅为家庭时期遗留兼容回退，SoloChef 真实运行时为空
        constraints.extend(state.get("user_constraints", []))
        constraints.extend(
            constraint for member in state.get("members", []) for constraint in member.constraints
        )
        constraints = list(dict.fromkeys(constraints))
        meal_text = " ".join(
            f"{meal.name} {' '.join(meal.tags)} {' '.join(meal.ingredients)}"
            for meal in draft.meals
        )
        forbidden_terms: set[str] = set()
        constraint_aliases = {
            "不吃辣": ["辣椒", "辣酱", "麻辣"],
            "乳糖不耐": ["牛奶", "奶油", "乳制品"],
            "海鲜过敏": ["虾", "蟹", "贝", "海鲜"],
        }
        for constraint in constraints:
            forbidden_terms.update(constraint_aliases.get(constraint, []))
            if constraint.endswith("过敏"):
                forbidden_terms.add(constraint.removesuffix("过敏"))
            if constraint.startswith("不吃"):
                forbidden_terms.add(constraint.removeprefix("不吃"))
            if constraint.startswith("忌"):
                forbidden_terms.add(constraint.removeprefix("忌"))
        violated_terms = sorted(term for term in forbidden_terms if term and term in meal_text)
        if violated_terms:
            warnings.append(f"菜单命中忌口或过敏食材：{'、'.join(violated_terms)}")

        expected_days = {"周一", "周二", "周三", "周四", "周五", "周六", "周日"}
        meal_days = [meal.day for meal in draft.meals]
        if len(draft.meals) != 7 or set(meal_days) != expected_days:
            missing = sorted(expected_days - set(meal_days))
            detail = f"，缺少 {'、'.join(missing)}" if missing else ""
            warnings.append(f"餐食未完整覆盖周一至周日{detail}")
        duplicate_meals = sorted(
            {
                meal.name
                for meal in draft.meals
                if sum(item.name == meal.name for item in draft.meals) > 1
            }
        )
        if duplicate_meals:
            warnings.append(f"一周菜单存在重复菜品：{'、'.join(duplicate_meals)}")

        category_total = sum(draft.budget.categories.values())
        if category_total > request.budget:
            warnings.append("预算分类合计超过总预算上限")
        domain_category_total = sum(domain.budget.category_limits.values())
        if domain_category_total + domain.budget.reserve > domain.budget.limit + 0.01:
            warnings.append("Budget Agent 的分类限额与预留金额超过总预算")
        # 第 6 项：营养目标达成率校验
        nutrition_targets = state.get("nutrition_targets") or {}
        if nutrition_targets:
            actual_nutrition: dict[str, float] = {}
            for meal in draft.meals:
                nutrition, _ = estimate_meal_nutrition(meal, [])
                for key, value in nutrition.items():
                    actual_nutrition[key] = actual_nutrition.get(key, 0.0) + value
            off_target: list[str] = []
            for key, target_value in nutrition_targets.items():
                if target_value <= 0:
                    continue
                actual_value = round(actual_nutrition.get(key, 0.0), 1)
                percent = actual_value / target_value * 100
                if percent < 90.0 or percent > 110.0:
                    direction = "不足" if percent < 90.0 else "超出"
                    detail = f"目标 {target_value}，实际 {actual_value}，{percent:.0f}%"
                    off_target.append(f"{key} {direction}（{detail}）")
            if off_target:
                warnings.append(f"营养目标偏差：{'；'.join(off_target)}")

        warnings = list(dict.fromkeys(warnings))
        output: dict[str, object] = {
            "constraints_checked": constraints,
            "forbidden_terms_checked": sorted(forbidden_terms),
            "warnings": warnings,
            "budget_usage_percent": draft.budget.usage_percent,
            "domain": domain.model_dump(mode="json"),
        }
        return {
            "draft": draft,
            "validation_warnings": warnings,
            "trace": [
                self._step(
                    start,
                    "verifier",
                    "Verifier Agent",
                    "执行预算、餐食完整性与用户忌口确定性校验",
                    output,
                    AgentStatus.WARNING if warnings else AgentStatus.COMPLETED,
                )
            ],
        }

    async def _final_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        sources: list[str] = []
        if state.get("graph_hits"):
            sources.append("Neo4j · 成员关系图谱")
        seen_documents: set[str] = set()
        for hit in state.get("vector_hits", []):
            if hit.document_name not in seen_documents:
                sources.append(f"Chroma · {hit.document_name}")
                seen_documents.add(hit.document_name)
        if not sources:
            sources.append("SoloChef · 无外部检索上下文的降级规划")
        output: dict[str, object] = {
            "sources": sources,
            "llm_mode": state["llm_mode"],
            "validation_warnings": state.get("validation_warnings", []),
        }
        return {
            "sources": sources,
            "trace": [
                self._step(
                    start,
                    "final_planner",
                    "Final Planner",
                    "汇总可执行计划、来源与校验结果",
                    output,
                )
            ],
        }

    @staticmethod
    def _step(
        start: float,
        name: str,
        label: str,
        summary: str,
        output: dict[str, object],
        status: AgentStatus = AgentStatus.COMPLETED,
    ) -> AgentStep:
        return AgentStep(
            name=name,
            label=label,
            status=status,
            duration_ms=max(1, round((perf_counter() - start) * 1000)),
            summary=summary,
            output=output,
        )


def to_agent_run(response: PlanningResponse, prompt: str) -> AgentRun:
    return AgentRun(
        id=response.run_id,
        request=prompt,
        status=AgentStatus.COMPLETED,
        started_at=datetime.now(UTC),
        duration_ms=sum(step.duration_ms for step in response.trace),
        steps=response.trace,
    )

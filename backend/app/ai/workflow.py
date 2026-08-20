import asyncio
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
from app.ai.intent_router import extract_planning_constraints
from app.ai.llm import (
    DemoPlanGenerator,
    LLMGenerationError,
    PlanDraft,
    PlanGenerator,
    validate_weekly_meals,
    with_meal_types,
)
from app.core.config import Settings, get_settings
from app.schemas import (
    AgentRun,
    GraphSearchHit,
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
    PlanConflict,
    ShoppingAgentResult,
)
from app.services.knowledge import get_knowledge_service
from app.services.plan_validation import (
    apply_auto_fix,
    compute_forbidden_terms,
    detect_conflicts,
    evaluate_manual_review,
)


class WorkflowState(TypedDict, total=False):
    request: PlanningRequest
    #: 单人用户画像的忌口/过敏约束（来自 UserProfile.constraints），SoloChef
    #: 忌口校验的优先数据源；members 仅为家庭时期遗留兼容回退
    user_constraints: list[str]
    #: 单人用户画像的饮食偏好（来自 UserProfile.preferences）
    user_preferences: list[str]
    #: 生活约束：最长备餐时间（分钟，来自 UserProfile.prep_time_max）
    prep_time_max: int | None
    #: 生活约束：可用厨具清单（来自 UserProfile.kitchenware）
    kitchenware: list[str]
    #: 历史执行反馈聚合出的口味画像，餐食智能体据此做偏好学习
    taste_profile: dict[str, object]
    #: 营养目标（TDEE + 宏量分配），由 planning_service 从 DB 加载后注入
    nutrition_targets: dict[str, float]
    #: 用户营养目标取向（bulk/cut/maintain），注入向量检索做目标型文档过滤
    goal_type: str | None
    planning_constraints: dict[str, object]
    graph_hits: list[GraphSearchHit]
    vector_hits: list[VectorSearchHit]
    graph_status: str
    vector_status: str
    context: str
    draft: PlanDraft
    llm_mode: str
    validation_warnings: list[str]
    conflict_details: list[PlanConflict]
    auto_fixes: list[str]
    needs_manual_review: bool
    manual_review_hint: str
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
    ) -> tuple[list[GraphSearchHit], str]: ...

    async def retrieve_vector(
        self,
        query: str,
        user_id: int,
        top_k: int,
        *,
        goal_type: str | None = None,
        meal_time: str | None = None,
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
            use_llm=(
                self._settings.domain_agents_llm_enabled
                and self._generator.mode != "demo"
            ),
        )
        self._checkpointer: BaseCheckpointSaver[str] | None = None
        self._graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(WorkflowState)
        builder.add_node("constraint_parser", self._constraint_parser_node)
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
        builder.add_edge(START, "constraint_parser")
        builder.add_edge("constraint_parser", "graph_retriever")
        builder.add_edge("constraint_parser", "vector_retriever")
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
        run_id: UUID | None = None,
        on_step: Callable[[AgentStep], Awaitable[None]] | None = None,
        resume: bool = False,
        taste_profile: dict[str, object] | None = None,
        nutrition_targets: dict[str, float] | None = None,
        user_constraints: Sequence[str] = (),
        user_preferences: Sequence[str] = (),
        prep_time_max: int | None = None,
        kitchenware: Sequence[str] = (),
        goal_type: str | None = None,
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
                "taste_profile": dict(taste_profile or {}),
                "nutrition_targets": dict(nutrition_targets or {}),
                "user_constraints": list(user_constraints),
                "user_preferences": list(user_preferences),
                "prep_time_max": prep_time_max,
                "kitchenware": list(kitchenware),
                "goal_type": goal_type,
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
            domain=state["domain_bundle"],
            sources=state["sources"],
            trace=state["trace"],
            conflict_details=state.get("conflict_details", []),
            auto_fixes=state.get("auto_fixes", []),
            needs_manual_review=state.get("needs_manual_review", False),
            manual_review_hint=state.get("manual_review_hint", ""),
        )

    async def _constraint_parser_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        constraints = {
            **extract_planning_constraints(request.prompt),
            "user_id": request.user_id,
            "budget": request.budget,
            "workflow": "weekly_plan",
        }
        return {
            "planning_constraints": constraints,
            "trace": [
                self._step(
                    start,
                    "constraint_parser",
                    "Constraint Parser",
                    "解析周计划预算、日期、餐次与营养硬约束",
                    constraints,
                )
            ],
        }

    async def _graph_retriever_node(self, state: WorkflowState) -> dict[str, object]:
        start = perf_counter()
        request = state["request"]
        hits, status = await self._knowledge.retrieve_graph(
            request.prompt,
            request.user_id,
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
            request.prompt,
            request.user_id,
            self._settings.rag_top_k,
            goal_type=state.get("goal_type"),
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
                    f"从向量库召回 {len(hits)} 个语义相关知识片段",
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
            draft = await asyncio.wait_for(
                self._generator.generate(request, state["context"] + specialist_context),
                timeout=self._settings.plan_generation_timeout_seconds,
            )
            draft.meals = with_meal_types(draft.meals)
            validate_weekly_meals(draft.meals)
        except (LLMGenerationError, TimeoutError) as exc:
            if not self._settings.ai_fallback_enabled:
                raise
            draft = await DemoPlanGenerator().generate(
                request, state["context"] + specialist_context
            )
            validate_weekly_meals(draft.meals)
            mode = f"{mode}->demo-fallback"
            status = AgentStatus.WARNING
            fallback_reason = (
                "主规划模型响应超时，已使用本地规划器完成生成"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
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
            state.get("taste_profile"),
            constraints=state.get("user_constraints", []),
            preferences=state.get("user_preferences", []),
            prep_time_max=state.get("prep_time_max"),
            kitchenware=state.get("kitchenware", []),
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
            state["request"]
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
            state["request"]
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
        domain = state["domain_bundle"]

        # 采购清单是计划落库后的实际估价来源，不能使用模型自报金额覆盖它。
        shopping_categories: dict[str, float] = {}
        for item in draft.shopping:
            shopping_categories[item.category] = round(
                shopping_categories.get(item.category, 0.0) + float(item.price), 2
            )
        shopping_estimated = round(sum(shopping_categories.values()), 2)
        draft.budget.estimated = shopping_estimated
        draft.budget.limit = request.budget
        draft.budget.saved = request.budget - shopping_estimated
        draft.budget.usage_percent = round(draft.budget.estimated / request.budget * 100)
        draft.budget.categories = shopping_categories

        # 忌口约束聚合（图谱关系 + SoloChef 单人画像，去重）
        constraints = [
            hit.target for hit in state.get("graph_hits", []) if hit.relation == "HAS_CONSTRAINT"
        ]
        constraints.extend(state.get("user_constraints", []))
        constraints = list(dict.fromkeys(constraints))
        forbidden_terms = compute_forbidden_terms(constraints)
        nutrition_targets = state.get("nutrition_targets") or {}

        # 校验失败三级策略：第 1 级自动修正（重复→缺天→营养→预算，最多 2 轮）
        conflicts = detect_conflicts(
            draft.meals,
            budget_limit=request.budget,
            constraints=constraints,
            category_limits=domain.budget.category_limits,
            category_limit_total=domain.budget.limit,
            category_reserve=domain.budget.reserve,
            nutrition_targets=nutrition_targets,
        )
        auto_fixes: list[str] = []
        for _ in range(2):
            soft = [conflict for conflict in conflicts if conflict.level == "soft"]
            if not soft:
                break
            draft.meals, fixes = apply_auto_fix(
                draft.meals,
                soft,
                forbidden_terms=forbidden_terms,
                nutrition_targets=nutrition_targets,
                budget_limit=request.budget,
            )
            if not fixes:
                break
            auto_fixes.extend(fixes)
            # 每轮自动修正后重跑校验，捕获"修了重复又超预算"的震荡
            conflicts = detect_conflicts(
                draft.meals,
                budget_limit=request.budget,
                constraints=constraints,
                category_limits=domain.budget.category_limits,
                category_limit_total=domain.budget.limit,
                category_reserve=domain.budget.reserve,
                nutrition_targets=nutrition_targets,
            )

        if shopping_estimated > request.budget:
            conflicts.append(
                PlanConflict(
                    dimension="budget",
                    level="hard",
                    message=(
                        f"采购清单估价 {shopping_estimated:.0f} 元超过预算 "
                        f"{request.budget:.0f} 元"
                    ),
                    item="采购预算",
                )
            )

        # 第 3 级人工接管判定：硬冲突率 > 30% → 提示放宽条件
        needs_manual_review, manual_review_hint = evaluate_manual_review(
            conflicts, len(draft.meals)
        )
        if shopping_estimated > request.budget:
            needs_manual_review = True
            manual_review_hint = "采购估价超过预算，请调整餐食、采购数量或预算后再确认"

        # 扁平化冲突信息（向后兼容 PlanningResponse.conflicts / WeeklyPlan.conflicts）
        flat_conflicts = [conflict.message for conflict in conflicts]
        draft.conflicts = list(dict.fromkeys([*draft.conflicts, *flat_conflicts]))
        if auto_fixes:
            auto_fixes = [f"已自动调整 {len(auto_fixes)} 处", *auto_fixes]

        output: dict[str, object] = {
            "constraints_checked": constraints,
            "forbidden_terms_checked": sorted(forbidden_terms),
            "warnings": flat_conflicts,
            "conflict_count": len(conflicts),
            "auto_fixes": auto_fixes,
            "needs_manual_review": needs_manual_review,
            "manual_review_hint": manual_review_hint,
            "budget_usage_percent": draft.budget.usage_percent,
            "domain": domain.model_dump(mode="json"),
        }
        return {
            "draft": draft,
            "validation_warnings": flat_conflicts,
            "conflict_details": conflicts,
            "auto_fixes": auto_fixes,
            "needs_manual_review": needs_manual_review,
            "manual_review_hint": manual_review_hint,
            "trace": [
                self._step(
                    start,
                    "verifier",
                    "Verifier Agent",
                    "执行三级校验失败策略（自动修正→降级提示→人工接管）",
                    output,
                    AgentStatus.WARNING if conflicts else AgentStatus.COMPLETED,
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
                sources.append(f"向量检索 · {hit.document_name}")
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

"""Executable LangGraph router for SoloChef's free-text assistant entry."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.ai.intent_router import intent_router
from app.schemas.intent import (
    IntentDecision,
    IntentEntryContext,
    IntentHandoff,
    IntentHandoffKind,
    IntentOperation,
    IntentRoute,
)


class AssistantRouterState(TypedDict, total=False):
    prompt: str
    has_active_plan: bool
    entry_context: IntentEntryContext
    decision: IntentDecision
    handoff: IntentHandoff
    router_trace: Annotated[list[str], operator.add]


class AssistantRouterWorkflow:
    """Classify once, then execute exactly one conditional handoff branch."""

    def __init__(self) -> None:
        self._graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(AssistantRouterState)
        builder.add_node("intent", self._intent_node)
        builder.add_node("weekly_plan_subgraph", self._weekly_plan_handoff)
        builder.add_node("shopping_subgraph", self._shopping_handoff)
        builder.add_node("budget_subgraph", self._budget_handoff)
        builder.add_node("consultation_subgraph", self._consultation_handoff)
        builder.add_node("plan_revision_subgraph", self._revision_handoff)
        builder.add_edge(START, "intent")
        builder.add_conditional_edges(
            "intent",
            self._select_route,
            {route.value: route.value for route in IntentRoute},
        )
        for route in IntentRoute:
            builder.add_edge(route.value, END)
        return builder.compile()

    async def route(
        self,
        prompt: str,
        *,
        has_active_plan: bool,
        entry_context: IntentEntryContext = IntentEntryContext.ASSISTANT,
    ) -> IntentDecision:
        state = await self._graph.ainvoke(
            {
                "prompt": prompt,
                "has_active_plan": has_active_plan,
                "entry_context": entry_context,
                "router_trace": [],
            }
        )
        return state["decision"].model_copy(
            update={
                "handoff": state["handoff"],
                "router_trace": state["router_trace"],
            }
        )

    @staticmethod
    def _intent_node(state: AssistantRouterState) -> dict[str, object]:
        decision = intent_router.classify(
            state["prompt"],
            has_active_plan=state["has_active_plan"],
            entry_context=state["entry_context"],
        )
        return {"decision": decision, "router_trace": ["intent"]}

    @staticmethod
    def _select_route(state: AssistantRouterState) -> str:
        return state["decision"].route.value

    @staticmethod
    def _weekly_plan_handoff(state: AssistantRouterState) -> dict[str, object]:
        return {
            "handoff": IntentHandoff(
                kind=IntentHandoffKind.PLANNER,
                path="/planner",
                mode="generate",
                prompt=state["prompt"],
                needs_confirmation=True,
                message="已进入周计划生成流程，确认预算后将生成完整 7 天 21 餐。",
            ),
            "router_trace": ["weekly_plan_subgraph"],
        }

    @staticmethod
    def _shopping_handoff(state: AssistantRouterState) -> dict[str, object]:
        decision = state["decision"]
        if decision.operation is IntentOperation.QUERY:
            return AssistantRouterWorkflow._chat_handoff(
                "shopping_subgraph", "我会结合当前购物清单回答这个问题。"
            )
        return {
            "handoff": IntentHandoff(
                kind=IntentHandoffKind.SHOPPING,
                path="/shopping",
                prompt=state["prompt"],
                needs_confirmation=False,
                message="已打开购物清单，具体增删改操作仍由你在页面中确认。",
            ),
            "router_trace": ["shopping_subgraph"],
        }

    @staticmethod
    def _budget_handoff(state: AssistantRouterState) -> dict[str, object]:
        decision = state["decision"]
        if decision.operation is IntentOperation.QUERY:
            return AssistantRouterWorkflow._chat_handoff(
                "budget_subgraph", "我会结合当前计划和预算数据回答这个问题。"
            )
        mode = "revise" if state["has_active_plan"] else "generate"
        return {
            "handoff": IntentHandoff(
                kind=IntentHandoffKind.PLANNER,
                path="/planner",
                mode=mode,
                prompt=state["prompt"],
                needs_confirmation=True,
                message=(
                    "已进入预算调整预览，确认前不会修改当前计划。"
                    if mode == "revise"
                    else "已进入周计划生成流程，请先确认本周预算。"
                ),
            ),
            "router_trace": ["budget_subgraph"],
        }

    @staticmethod
    def _consultation_handoff(state: AssistantRouterState) -> dict[str, object]:
        return AssistantRouterWorkflow._chat_handoff(
            "consultation_subgraph", "我会结合你的档案与知识库回答。"
        )

    @staticmethod
    def _revision_handoff(state: AssistantRouterState) -> dict[str, object]:
        return {
            "handoff": IntentHandoff(
                kind=IntentHandoffKind.PLANNER,
                path="/planner",
                mode="revise",
                prompt=state["prompt"],
                needs_confirmation=True,
                message="已进入计划调整，先查看完整新版本预览，再决定是否保存。",
            ),
            "router_trace": ["plan_revision_subgraph"],
        }

    @staticmethod
    def _chat_handoff(branch: str, message: str) -> dict[str, object]:
        return {
            "handoff": IntentHandoff(
                kind=IntentHandoffKind.CHAT,
                needs_confirmation=False,
                message=message,
            ),
            "router_trace": [branch],
        }


assistant_router_workflow = AssistantRouterWorkflow()

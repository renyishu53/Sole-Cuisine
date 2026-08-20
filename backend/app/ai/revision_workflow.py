"""Conditional LangGraph router for plan-revision domain impact."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.intent import IntentCapability
from app.schemas.plan_revise import (
    PlanDiff,
    ReviseOperation,
    RevisionRoute,
    RevisionRouteDecision,
)

AppliedRevision: TypeAlias = tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    float,
    PlanDiff,
]
ApplyRevision: TypeAlias = Callable[[Any, ReviseOperation, list[Any]], AppliedRevision]


class RevisionWorkflowState(TypedDict, total=False):
    plan: Any
    operation: ReviseOperation
    recipes: list[Any]
    routing: RevisionRouteDecision
    applied: AppliedRevision
    affected_agents: list[IntentCapability]
    dependency_synced: bool


class PlanRevisionWorkflow:
    """Route a parsed revision and execute only its selected domain branch."""

    def __init__(self, apply_revision: ApplyRevision) -> None:
        self._apply_revision = apply_revision
        self._graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(RevisionWorkflowState)
        builder.add_node("revision_intent", self._route_revision)
        for route in RevisionRoute:
            builder.add_node(route.value, self._execute_selected_branch)
        builder.add_node("affected_agents", self._affected_agents_node)
        builder.add_node("dependency_sync", self._dependency_sync_node)
        builder.add_edge(START, "revision_intent")
        builder.add_conditional_edges(
            "revision_intent",
            self._select_route,
            {route.value: route.value for route in RevisionRoute},
        )
        for route in RevisionRoute:
            builder.add_edge(route.value, "affected_agents")
        builder.add_edge("affected_agents", "dependency_sync")
        builder.add_edge("dependency_sync", END)
        return builder.compile()

    async def run(
        self, plan: Any, operation: ReviseOperation, recipes: list[Any]
    ) -> tuple[RevisionRouteDecision, AppliedRevision]:
        state = await self._graph.ainvoke(
            {"plan": plan, "operation": operation, "recipes": recipes}
        )
        return state["routing"], state["applied"]

    @staticmethod
    def _route_revision(state: RevisionWorkflowState) -> dict[str, object]:
        operation = state["operation"].operation
        if operation == "adjust_shopping":
            routing = RevisionRouteDecision(
                route=RevisionRoute.SHOPPING,
                requires=[
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.VERIFIER,
                ],
                reason="购物项变化需要同步重算采购估价并校验预算",
            )
        elif operation == "update_budget":
            routing = RevisionRouteDecision(
                route=RevisionRoute.BUDGET,
                requires=[IntentCapability.BUDGET, IntentCapability.VERIFIER],
                reason="预算上限变化只进入预算校验分支",
            )
        elif operation == "adjust_macro_target":
            routing = RevisionRouteDecision(
                route=RevisionRoute.CONSTRAINT,
                requires=[
                    IntentCapability.MEAL,
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.VERIFIER,
                ],
                reason="营养目标属于上游约束，确认后需要完整重算计划",
            )
        elif operation == "exclude_ingredient":
            routing = RevisionRouteDecision(
                route=RevisionRoute.COMPOUND,
                requires=[
                    IntentCapability.MEAL,
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.VERIFIER,
                ],
                reason="排除食材同时影响餐食、购物清单和预算",
            )
        else:
            routing = RevisionRouteDecision(
                route=RevisionRoute.MEAL,
                requires=[
                    IntentCapability.MEAL,
                    IntentCapability.SHOPPING,
                    IntentCapability.BUDGET,
                    IntentCapability.VERIFIER,
                ],
                reason="餐食变化需要联动购物清单、预算与冲突校验",
            )
        return {"routing": routing}

    @staticmethod
    def _select_route(state: RevisionWorkflowState) -> str:
        return state["routing"].route.value

    def _execute_selected_branch(
        self, state: RevisionWorkflowState
    ) -> dict[str, AppliedRevision]:
        return {
            "applied": self._apply_revision(
                state["plan"], state["operation"], state["recipes"]
            )
        }

    @staticmethod
    def _affected_agents_node(state: RevisionWorkflowState) -> dict[str, object]:
        """Expose the selected domain capabilities as the business-level stage.

        The branch has already produced a deterministic draft. This stage makes
        the selected downstream capabilities explicit for preview/audit consumers.
        """
        requires = state["routing"].requires
        return {
            "affected_agents": [
                capability
                for capability in requires
                if capability is not IntentCapability.VERIFIER
            ]
        }

    @staticmethod
    def _dependency_sync_node(state: RevisionWorkflowState) -> dict[str, object]:
        """Mark the meal/shopping/budget dependency barrier as completed."""
        if "applied" not in state:
            raise RuntimeError("revision branch did not produce an applied draft")
        return {"dependency_synced": True}

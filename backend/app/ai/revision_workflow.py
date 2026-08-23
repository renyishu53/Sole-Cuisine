"""Deterministic router for plan-revision domain impact.

Plan revision currently has no model/tool loop, checkpoint, or graph cycle. A
typed function is therefore easier to reason about than a LangGraph wrapper,
while the returned routing contract remains unchanged for the API and UI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

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


def route_revision(operation: ReviseOperation) -> RevisionRouteDecision:
    """Map a parsed operation to affected planning capabilities."""
    operation_type = operation.operation
    if operation_type == "update_budget":
        routing = RevisionRouteDecision(
            route=RevisionRoute.BUDGET,
            requires=[IntentCapability.BUDGET, IntentCapability.VERIFIER],
            reason="预算上限变化只进入预算校验分支",
        )
    elif operation_type == "adjust_macro_target":
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
    elif operation_type == "exclude_ingredient":
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
    return routing


def run_revision_workflow(
    plan: Any,
    operation: ReviseOperation,
    recipes: list[Any],
    apply_revision: ApplyRevision,
) -> tuple[RevisionRouteDecision, AppliedRevision]:
    """Route and apply a deterministic plan revision.

    The function deliberately has no hidden state or graph lifecycle. The
    caller owns persistence and confirmation, so preview generation remains
    side-effect free until the existing confirm endpoint is called.
    """
    routing = route_revision(operation)
    applied = apply_revision(plan, operation, recipes)
    return routing, applied

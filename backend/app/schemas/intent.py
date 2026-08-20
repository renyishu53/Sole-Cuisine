"""Structured contracts for assistant intent routing."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssistantIntent(StrEnum):
    WEEKLY_PLAN = "weekly_plan"
    SHOPPING = "shopping"
    BUDGET = "budget"
    CONSULTATION = "consultation"
    PLAN_REVISION = "plan_revision"


class IntentOperation(StrEnum):
    CREATE = "create"
    REGENERATE = "regenerate"
    REVISE = "revise"
    QUERY = "query"


class IntentCapability(StrEnum):
    MEAL = "meal"
    SHOPPING = "shopping"
    BUDGET = "budget"
    RETRIEVAL = "retrieval"
    VERIFIER = "verifier"


class IntentRoute(StrEnum):
    WEEKLY_PLAN = "weekly_plan_subgraph"
    SHOPPING = "shopping_subgraph"
    BUDGET = "budget_subgraph"
    CONSULTATION = "consultation_subgraph"
    PLAN_REVISION = "plan_revision_subgraph"


class IntentEntryContext(StrEnum):
    ASSISTANT = "assistant"
    PLANNER_GENERATE = "planner_generate"
    PLANNER_REVISION = "planner_revision"


class IntentHandoffKind(StrEnum):
    CHAT = "chat"
    PLANNER = "planner"
    SHOPPING = "shopping"


class IntentHandoff(BaseModel):
    kind: IntentHandoffKind
    path: str | None = None
    mode: str | None = None
    prompt: str | None = None
    needs_confirmation: bool = False
    message: str


class IntentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=2, max_length=4000)
    has_active_plan: bool = False
    entry_context: IntentEntryContext = IntentEntryContext.ASSISTANT


class IntentDecision(BaseModel):
    intent: AssistantIntent
    operation: IntentOperation
    requires: list[IntentCapability]
    confidence: float = Field(ge=0, le=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    route: IntentRoute
    reason: str
    needs_clarification: bool = False
    entry_context: IntentEntryContext = IntentEntryContext.ASSISTANT
    handoff: IntentHandoff | None = None
    router_trace: list[str] = Field(default_factory=list)

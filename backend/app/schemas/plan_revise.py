"""备餐规划局部修改 schema。

PlanReviseService 把用户的自然语言修改要求（如"把周三晚餐换成鸡胸肉"）
经 LLM 解析为结构化 :class:`ReviseOperation`，再由业务层执行局部修改，
最终输出 before/after :class:`PlanSnapshot` 与 :class:`PlanDiff` 预览。

设计要点：
- 八种 operation 覆盖常见修改场景（替换/移除/添加餐食、排除食材、
  调整预算、跳过某天、调整营养目标与购物项）。
- ``target`` / ``constraints`` 用 ``dict[str, Any]`` 而非嵌套模型，便于 LLM
  灵活输出；业务层按 operation 类型解释字段语义。
- :class:`RevisePreviewResponse` 不持久化，存到 ``ChatMessage.payload`` 供
  前端展示历史对话与可恢复的预览。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentCapability

# 支持的修改操作枚举。Literal 用于 LLM schema 约束 + 业务层 type-narrow。
ReviseOperationType = Literal[
    "replace_meal",        # 替换某天某餐（如"周三晚餐换鸡胸肉"）
    "remove_meal",         # 删除某天某餐
    "add_meal",            # 添加某天某餐
    "exclude_ingredient",  # 从所有餐食+购物清单排除食材（如"不要牛奶"）
    "update_budget",       # 调整预算（如"总预算降到 300 元"）
    "skip_day",            # 跳过某天不做饭（如"周末外食"）
    "adjust_macro_target", # 调整营养目标（如"蛋白质提高到每天 120g"）
    "adjust_shopping",     # 增删改计划关联的购物项
]


class RevisionRoute(StrEnum):
    MEAL = "meal_revision_subgraph"
    SHOPPING = "shopping_revision_subgraph"
    BUDGET = "budget_revision_subgraph"
    CONSTRAINT = "constraint_revision_subgraph"
    COMPOUND = "compound_revision_subgraph"


class RevisionRouteDecision(BaseModel):
    route: RevisionRoute
    requires: list[IntentCapability]
    reason: str


class ReviseOperation(BaseModel):
    """LLM 解析出的结构化修改指令。

    ``target`` 与 ``constraints`` 字段语义按 ``operation`` 类型解释：

    - ``replace_meal``: target={day, meal_type}, proposal=新餐食
      —— service 用 day + meal_type 模糊匹配现有 PlanMealItem（按 name 含关键词）
    - ``remove_meal``: target={day, meal_type}
    - ``add_meal``: target={day, meal_type}, proposal=新餐食
    - ``exclude_ingredient``: target={ingredient}
    - ``update_budget``: target={budget_limit}
    - ``skip_day``: target={day}
    - ``adjust_macro_target``: target={protein_g?, carbs_g?, fat_g?, calories?}
    - ``adjust_shopping``: target={action, name, quantity?, price?}
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    operation: ReviseOperationType
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="操作目标，如 {day: '周三', meal_type: '晚餐'}",
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="操作约束，如 {max_calories: 550, exclude_duplicates: true}",
    )
    proposal: MealProposal | None = Field(
        default=None,
        description="新餐食提案，仅 replace_meal / add_meal 操作使用",
    )
    reason: str = Field(default="", max_length=500)


class MealProposal(BaseModel):
    """LLM 在 ``replace_meal`` / ``add_meal`` 时提议的新餐食。

    与 :class:`~app.schemas.domain.MealItem` 字段对齐，但 ``id`` 留空——
    新餐食落库时由 ORM 分配。``meal_type`` 作为 ``tags`` 的一部分持久化，
    便于前端按"早/中/晚"筛选展示。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    day: str = Field(min_length=1, max_length=10)
    meal_type: str = Field(default="晚餐", max_length=10, description="早/中/晚，仅用于检索")
    name: str = Field(min_length=1, max_length=120, description="餐食名，如 '香煎鸡胸肉配西兰花'")
    duration: int = Field(default=30, ge=1, le=1440)
    cost: float = Field(default=0, ge=0, le=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=500)
    ingredients: list[str] = Field(default_factory=list, max_length=100)

    def to_meal_item_dict(self) -> dict[str, Any]:
        """转换为 PlanMealItem 兼容的 dict（meal_type 合并进 tags）。"""
        merged_tags = list(self.tags)
        if self.meal_type and self.meal_type not in merged_tags:
            merged_tags.insert(0, self.meal_type)
        return {
            "day": self.day,
            "meal_type": self.meal_type,
            "name": self.name,
            "duration": self.duration,
            "cost": self.cost,
            "tags": merged_tags,
            "reason": self.reason,
            "ingredients": list(self.ingredients),
        }


class NutritionSnapshot(BaseModel):
    """计划级营养快照（用于 before/after 对比）。"""

    calories: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carbs_g: float = 0.0


class BudgetSnapshot(BaseModel):
    """计划级预算快照。"""

    limit: float = 0.0
    estimated: float = 0.0
    saved: float = 0.0
    usage_percent: int = 0
    categories: dict[str, float] = Field(default_factory=dict)


class PlanSnapshot(BaseModel):
    """计划快照——某一时刻的餐食/购物/营养/预算状态。

    用于在 :class:`RevisePreviewResponse` 中并排展示 before/after。
    """

    meals: list[dict[str, Any]] = Field(default_factory=list)
    shopping: list[dict[str, Any]] = Field(default_factory=list)
    nutrition: NutritionSnapshot = Field(default_factory=NutritionSnapshot)
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)


class PlanDiff(BaseModel):
    """修改前后差异摘要。

    ``changed_meals`` / ``changed_shopping`` 是变更项的简要描述列表，
    前端可直接渲染为"修改了什么"列表。``nutrition_delta`` / ``budget_delta``
    是数值差异（after − before），保留一位小数。
    """

    changed_meals: list[str] = Field(default_factory=list)
    changed_shopping: list[str] = Field(default_factory=list)
    nutrition_delta: dict[str, float] = Field(default_factory=dict)
    budget_delta: dict[str, float] = Field(default_factory=dict)
    conflict_warnings: list[str] = Field(
        default_factory=list,
        description="联动检查发现的冲突，如忌口命中、菜品重复、预算超限",
    )


class ReviseRequest(BaseModel):
    """POST /plans/{plan_id}/revise 请求体。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=2, max_length=1000)
    session_id: str | None = Field(
        default=None,
        description="可选：复用已有对话会话；缺省时按 plan_id 复用最近会话或新建",
    )


class RevisePreviewResponse(BaseModel):
    """修改预览响应——不持久化计划，仅返回 before/after 供前端展示。

    前端确认后调 POST /plans/{plan_id}/revise/{revise_id}/confirm 落库。
    """

    revise_id: str
    plan_id: int
    plan_version: int
    operation: ReviseOperation
    routing: RevisionRouteDecision
    summary: str = Field(description="人类可读的修改说明，用于对话气泡")
    before: PlanSnapshot
    after: PlanSnapshot
    diff: PlanDiff
    can_confirm: bool = Field(
        default=False,
        description="before/after 是否存在可落库的实际变化",
    )
    message_id: int | None = Field(
        default=None,
        description="assistant 消息 ID，前端可据此回溯对话历史",
    )


class ReviseConfirmResponse(BaseModel):
    """确认修改响应——新版本已落库。"""

    revise_id: str
    plan_id: int
    new_plan_id: int
    new_version: int
    parent_plan_id: int
    summary: str

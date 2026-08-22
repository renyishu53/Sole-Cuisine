from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.shopping_categories import normalize_shopping_category


class TaskStatus(StrEnum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"


class AgentStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"


class VisionScene(StrEnum):
    """多模态图片识别场景。"""

    AUTO = "auto"  # 由视觉模型自动判断图片类型
    INGREDIENT = "ingredient"  # 食材识别
    DISH = "dish"  # 菜品 + 热量估算
    NUTRITION_LABEL = "label"  # 营养成分表 OCR
    RECEIPT = "receipt"  # 购物小票 OCR


class VisionResult(BaseModel):
    """视觉识别统一响应结构。"""

    scene: VisionScene
    summary: str
    items: list[dict[str, Any]] = []
    calories: float | None = None
    raw_text: str = ""


class ResearchResult(BaseModel):
    """A bounded, auditable item returned by an external research provider."""

    provider: str = Field(min_length=1, max_length=40)
    title: str = Field(max_length=200)
    url: str = Field(max_length=500)
    snippet: str = Field(max_length=1_000)
    fetched_at: datetime
    status: Literal["ok", "warning"] = "ok"


class MemberProfile(BaseModel):
    id: int
    name: str
    role: str
    avatar: str
    color: str
    preferences: list[str]
    constraints: list[str]
    availability: str
    age_group: str = "成年人"
    notes: str = ""
    is_account_linked: bool = False


class MemberProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(default="成员", min_length=1, max_length=30)
    age_group: str = Field(default="成年人", min_length=1, max_length=30)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    availability: str = Field(default="待补充", min_length=1, max_length=200)
    notes: str = Field(default="", max_length=500)
    avatar: str = Field(default="我", min_length=1, max_length=8)
    color: str = Field(default="#46705d", pattern=r"^#[0-9A-Fa-f]{6}$")


class MemberProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    relationship: str | None = Field(default=None, min_length=1, max_length=30)
    age_group: str | None = Field(default=None, min_length=1, max_length=30)
    preferences: list[str] | None = Field(default=None, max_length=20)
    constraints: list[str] | None = Field(default=None, max_length=20)
    availability: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=500)
    avatar: str | None = Field(default=None, min_length=1, max_length=8)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def ensure_update_has_values(self) -> "MemberProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class CalendarEvent(BaseModel):
    id: int
    title: str
    member: str
    day: str
    time: str
    category: str
    conflict: bool = False
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    location: str = ""
    notes: str = ""
    participant_ids: list[int] = Field(default_factory=list)
    participants: list["CalendarParticipant"] = Field(default_factory=list)
    recurrence: "CalendarRecurrenceRule" = Field(default_factory=lambda: CalendarRecurrenceRule())
    occurrence_start_at: datetime | None = None
    occurrence_end_at: datetime | None = None


class CalendarParticipant(BaseModel):
    member_id: int
    name: str
    role: str
    avatar: str
    color: str


class CalendarRecurrenceRule(BaseModel):
    type: Literal["none", "daily", "weekly", "monthly"] = "none"
    interval: int = Field(default=1, ge=1, le=52)
    days_of_week: list[int] = Field(default_factory=list, max_length=7)
    until: datetime | None = None
    count: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def validate_days(self) -> "CalendarRecurrenceRule":
        if any(day < 0 or day > 6 for day in self.days_of_week):
            raise ValueError("days_of_week must contain values from 0 to 6")
        if self.type != "weekly" and self.days_of_week:
            raise ValueError("days_of_week is only supported for weekly recurrence")
        return self


class CalendarEventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    start_at: datetime
    end_at: datetime
    participant_ids: list[int] = Field(min_length=1, max_length=30)
    category: str = Field(default="personal", min_length=1, max_length=40)
    location: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=500)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    recurrence: CalendarRecurrenceRule = Field(default_factory=CalendarRecurrenceRule)
    is_all_day: bool = False

    @model_validator(mode="after")
    def ensure_time_order(self) -> "CalendarEventCreate":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CalendarEventUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=120)
    start_at: datetime | None = None
    end_at: datetime | None = None
    participant_ids: list[int] | None = Field(default=None, min_length=1, max_length=30)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    location: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    recurrence: CalendarRecurrenceRule | None = None
    is_all_day: bool | None = None

    @model_validator(mode="after")
    def ensure_update_is_valid(self) -> "CalendarEventUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if self.start_at is not None and self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CalendarConflictCheckRequest(BaseModel):
    start_at: datetime
    end_at: datetime
    participant_ids: list[int] = Field(min_length=1, max_length=30)
    recurrence: CalendarRecurrenceRule = Field(default_factory=CalendarRecurrenceRule)
    exclude_event_id: int | None = None

    @model_validator(mode="after")
    def ensure_time_order(self) -> "CalendarConflictCheckRequest":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CalendarConflict(BaseModel):
    event_id: int
    title: str
    start_at: datetime
    end_at: datetime
    participant_ids: list[int]
    participants: list[str]


class CalendarConflictCheckResponse(BaseModel):
    has_conflict: bool
    conflicts: list[CalendarConflict]


class CalendarOccurrenceExceptionCreate(BaseModel):
    occurrence_start_at: datetime
    action: Literal["cancel", "modify"] = "cancel"
    override: dict[str, object] = Field(default_factory=dict)


class CalendarOccurrenceException(BaseModel):
    id: int
    event_id: int
    occurrence_start_at: datetime
    action: str
    override: dict[str, object]


class CalendarAlternativeSlot(BaseModel):
    start_at: datetime
    end_at: datetime
    participant_ids: list[int]
    label: str


class CalendarAgentConflict(BaseModel):
    event_ids: list[int]
    titles: list[str]
    start_at: datetime
    end_at: datetime
    participant_ids: list[int]
    participants: list[str]
    message: str


class CalendarAgentResult(BaseModel):
    status: Literal["clear", "conflict"]
    has_conflict: bool
    checked_event_count: int
    affected_member_ids: list[int] = Field(default_factory=list)
    conflicts: list[CalendarAgentConflict] = Field(default_factory=list)
    alternative_slots: list[CalendarAlternativeSlot] = Field(default_factory=list)


class MealAgentResult(BaseModel):
    strategy: str
    constraints_applied: list[str]
    excluded_ingredients: list[str]
    preferred_tags: list[str]
    max_duration_minutes: int = Field(ge=5, le=240)


class ShoppingAgentResult(BaseModel):
    strategy: str
    merge_keys: list[str]
    preferred_categories: list[str]
    purchase_windows: list[str]


class TaskAssignmentCandidate(BaseModel):
    member_id: int
    member_name: str
    availability: str
    priority: int = Field(ge=1)


class TaskAgentResult(BaseModel):
    strategy: str
    fairness_rule: str
    candidates: list[TaskAssignmentCandidate]
    default_duration_minutes: int = Field(ge=5, le=240)


class BudgetSelfCheck(BaseModel):
    """预算分配自检字段：分类之和 + 预留 == 周预算。"""

    category_sum: float = 0
    total_check: float = 0
    expected: float = 0
    matched: bool = False


class BudgetAgentResult(BaseModel):
    strategy: str
    limit: float = Field(gt=0)
    reserve: float = Field(ge=0)
    warning_threshold_percent: int = Field(ge=1, le=100)
    category_limits: dict[str, float]
    self_check: BudgetSelfCheck | None = None


class DomainAgentBundle(BaseModel):
    meal: MealAgentResult
    shopping: ShoppingAgentResult
    budget: BudgetAgentResult
    merged_constraints: list[str]


class TaskItem(BaseModel):
    id: int
    title: str
    assignee: str
    duration: int
    due: str
    status: TaskStatus
    category: str
    assignee_member_id: int | None = None
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    recurrence_type: str = "none"
    recurrence_interval: int = 1


class TaskItemCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    assignee: str = Field(default="待分配", min_length=1, max_length=80)
    duration: int = Field(default=15, ge=1, le=1440)
    due: str = Field(default="待安排", min_length=1, max_length=50)
    status: TaskStatus = TaskStatus.TODO
    category: str = Field(default="日常", min_length=1, max_length=40)
    assignee_member_id: int | None = None
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    recurrence_type: Literal["none", "daily", "weekly", "monthly"] = "none"
    recurrence_interval: int = Field(default=1, ge=1, le=52)

    @model_validator(mode="after")
    def ensure_time_order(self) -> "TaskItemCreate":
        if (
            self.scheduled_start_at is not None
            and self.scheduled_end_at is not None
            and self.scheduled_end_at <= self.scheduled_start_at
        ):
            raise ValueError("scheduled_end_at must be after scheduled_start_at")
        return self


class TaskItemUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=120)
    assignee: str | None = Field(default=None, min_length=1, max_length=80)
    duration: int | None = Field(default=None, ge=1, le=1440)
    due: str | None = Field(default=None, min_length=1, max_length=50)
    status: TaskStatus | None = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    assignee_member_id: int | None = None
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    recurrence_type: Literal["none", "daily", "weekly", "monthly"] | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=52)

    @model_validator(mode="after")
    def ensure_values(self) -> "TaskItemUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        if (
            self.scheduled_start_at is not None
            and self.scheduled_end_at is not None
            and self.scheduled_end_at <= self.scheduled_start_at
        ):
            raise ValueError("scheduled_end_at must be after scheduled_start_at")
        return self


class MealItem(BaseModel):
    id: int = 0
    day: str
    meal_type: str = "晚餐"
    name: str
    duration: int
    cost: float
    tags: list[str]
    reason: str
    ingredients: list[str]
    # Phase 4：餐食"已吃"打卡 + 未吃偏差结构化
    eaten: bool = False
    eaten_at: datetime | None = None
    deviation_type: str | None = None
    deviation_reason: str = ""


class MealItemCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    day: str = Field(min_length=1, max_length=10)
    meal_type: str = Field(default="晚餐", max_length=10)
    name: str = Field(min_length=1, max_length=120)
    duration: int = Field(default=30, ge=1, le=1440)
    cost: float = Field(default=0, ge=0, le=100000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=500)
    ingredients: list[str] = Field(default_factory=list, max_length=100)


class MealItemUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    day: str | None = Field(default=None, min_length=1, max_length=10)
    meal_type: str | None = Field(default=None, max_length=10)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    duration: int | None = Field(default=None, ge=1, le=1440)
    cost: float | None = Field(default=None, ge=0, le=100000)
    tags: list[str] | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=500)
    ingredients: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def ensure_values(self) -> "MealItemUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ShoppingItem(BaseModel):
    id: int
    name: str
    category: str
    quantity: str
    price: float
    source: str
    origin: Literal["meal_ingredient", "extra_purchase"] = "meal_ingredient"
    purchased: bool = False
    # 食材替换确认闭环：substituted_from 记录被替换前的原食材名，
    # substituted_accepted 记录用户是否确认（None=待确认，True=已接受，False=已拒绝并回退）。
    substituted_from: str | None = None
    substituted_accepted: bool | None = None

    @model_validator(mode="after")
    def normalize_category(self) -> "ShoppingItem":
        self.category = normalize_shopping_category(self.category, self.name)
        return self


class ShoppingItemCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="其他", min_length=1, max_length=40)
    quantity: str = Field(default="1", min_length=1, max_length=40)
    price: float = Field(default=0, ge=0, le=100000)
    source: str = Field(default="手工添加", max_length=100)
    purchased: bool = False

    @model_validator(mode="after")
    def normalize_category(self) -> "ShoppingItemCreate":
        self.category = normalize_shopping_category(self.category, self.name)
        return self


class ShoppingItemUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    quantity: str | None = Field(default=None, min_length=1, max_length=40)
    price: float | None = Field(default=None, ge=0, le=100000)
    source: str | None = Field(default=None, max_length=100)
    origin: Literal["meal_ingredient", "extra_purchase"] | None = None
    purchased: bool | None = None
    # 核销反馈：仅在 purchased 由 false → true 时参与闭环回流，不落到 PlanShoppingItem
    actual_price: float | None = Field(default=None, ge=0, le=100000)
    verification_note: str | None = Field(default=None, max_length=500)

    #: 反馈字段不是购物条目的持久化列，写回 ORM 前需要剔除
    FEEDBACK_FIELDS: ClassVar[frozenset[str]] = frozenset({"actual_price", "verification_note"})

    @model_validator(mode="after")
    def ensure_values(self) -> "ShoppingItemUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self

    @model_validator(mode="after")
    def normalize_category(self) -> "ShoppingItemUpdate":
        if self.category is not None:
            self.category = normalize_shopping_category(self.category, self.name or "")
        return self

    def item_changes(self) -> dict[str, Any]:
        """只返回需要写回购物条目的字段。"""
        return {
            key: value
            for key, value in self.model_dump(exclude_unset=True).items()
            if key not in self.FEEDBACK_FIELDS
        }


class ShoppingImpactMeal(BaseModel):
    """餐食与购物条目的依赖关系摘要。"""

    id: int
    day: str
    meal_type: str
    name: str


class ShoppingImpactResponse(BaseModel):
    """描述修改购物条目是否会破坏当前计划的餐食依赖。"""

    item_id: int
    item_name: str
    has_impact: bool
    affected_meals: list[ShoppingImpactMeal] = Field(default_factory=list)
    message: str = ""


class MealReplacementRequest(BaseModel):
    feedback: str = Field(min_length=2, max_length=1000)
    rating: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=20)


MealDeviationType = Literal["not_available", "no_appetite", "ate_other"]


class MealCheckinRequest(BaseModel):
    """餐食"已吃"打卡请求：已吃时置 eaten=true；未吃时携带偏差类型与原因。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    eaten: bool
    deviation_type: MealDeviationType | None = None
    deviation_reason: str = Field(default="", max_length=500)


class TodayNutrient(BaseModel):
    """单个营养素的今日达成进度：目标 / 已摄入 / 剩余 / 达成率。"""

    target: float = 0
    consumed: float = 0
    remaining: float = 0
    percent: float = Field(default=0, description="已摄入占目标百分比，0-200 区间")


class TodayNutritionResponse(BaseModel):
    """今日营养目标达成进度（按当日已吃餐食聚合）。"""

    day: str = Field(description="今日中文星期标签，如 '周四'")
    meal_count: int = 0
    eaten_count: int = 0
    nutrients: dict[str, TodayNutrient] = Field(default_factory=dict)
    overall_percent: float = 0


class ShoppingMergeResponse(BaseModel):
    merged_groups: int
    removed_items: int
    items: list[ShoppingItem]
    conversion_notes: list[dict[str, Any]] = Field(default_factory=list)


class ExpenseHistoryItem(BaseModel):
    id: int
    category: str
    amount: float
    occurred_at: datetime
    note: str
    plan_id: int | None = None
    shopping_item_id: int | None = None


class ExpenseHistoryResponse(BaseModel):
    """采购历史查询响应，含明细列表与汇总统计。"""

    items: list[ExpenseHistoryItem]
    total_amount: float
    count: int
    by_category: dict[str, float]
    categories: list[str]


class TaskExpansionItem(BaseModel):
    """周期任务展开后的单个发生项。"""

    task_id: int
    title: str
    assignee: str
    category: str
    duration: int
    recurrence_type: str
    recurrence_interval: int
    occurrence_at: datetime


class TaskExpansionResponse(BaseModel):
    """任务自动展开响应。"""

    days: int
    count: int
    items: list[TaskExpansionItem]


class ExpenseCreate(BaseModel):
    category: str = Field(default="其他", min_length=1, max_length=40)
    amount: float = Field(gt=0, le=1000000)
    occurred_at: datetime
    note: str = Field(default="", max_length=500)
    shopping_item_id: int | None = None


class Expense(BaseModel):
    id: int
    category: str
    amount: float
    occurred_at: datetime
    note: str
    plan_id: int | None = None
    shopping_item_id: int | None = None


class BudgetAnalytics(BaseModel):
    limit: float
    actual_spent: float
    remaining: float
    usage_percent: int
    warning: bool
    by_category: dict[str, float]
    monthly_trend: dict[str, float]


class TaskAutoAssignResponse(BaseModel):
    assigned: int
    skipped: int
    tasks: list[TaskItem]


class TaskCompleteRequest(BaseModel):
    actual_duration: int = Field(default=0, ge=0, le=1440)
    notes: str = Field(default="", max_length=500)
    rating: int | None = Field(default=None, ge=1, le=5)

    def completion_values(self) -> dict[str, Any]:
        """仅返回 ``TaskCompletion`` 需要的列，评分只参与反馈闭环。"""
        return {"actual_duration": self.actual_duration, "notes": self.notes}


class FeedbackSyncInfo(BaseModel):
    """执行反馈的回流结果，让前端能直观看到"闭环是否合上"。"""

    feedback_id: int
    sentiment: Literal["positive", "neutral", "negative"]
    deviation: float = 0
    graph_synced: bool = False
    vector_synced: bool = False
    notes: list[str] = Field(default_factory=list)


class TaskCompletionResponse(BaseModel):
    id: int
    task_id: int
    member_profile_id: int | None = None
    completed_at: datetime
    actual_duration: int
    notes: str
    feedback: FeedbackSyncInfo | None = None


class FeedbackEntry(BaseModel):
    """``plan_feedback`` 偏差表的对外视图。"""

    id: int
    feedback_type: str
    reference_type: str
    reference_id: int
    subject: str
    tags: list[str] = Field(default_factory=list)
    rating: int | None = None
    sentiment: str
    content: str
    planned_value: float = 0
    actual_value: float = 0
    deviation: float = 0
    source: str
    synced_to_graph: bool = False
    synced_to_vector: bool = False
    created_at: datetime


class TasteProfileResponse(BaseModel):
    """餐食智能体使用的口味画像，同时供前端展示"系统学到了什么"。"""

    liked_tags: list[str] = Field(default_factory=list)
    disliked_tags: list[str] = Field(default_factory=list)
    liked_dishes: list[str] = Field(default_factory=list)
    rejected_dishes: list[str] = Field(default_factory=list)
    recent_notes: list[str] = Field(default_factory=list)
    sample_size: int = 0


class TasteDimension(BaseModel):
    """口味画像五维向量的一维：辣 / 清淡 / 甜 / 咸 / 酸。"""

    key: str
    label: str
    score: float  # -1..1，正值偏好、负值回避、0 中性/无数据


class TasteVectorResponse(BaseModel):
    """反馈复盘五维口味雷达的数据源（§7.4 已敲定的后端新增接口）。"""

    dimensions: list[TasteDimension]
    sample_size: int = 0


class FeedbackOverviewResponse(BaseModel):
    """反馈闭环总览：偏差明细 + 情感分布 + 待补偿同步数。"""

    items: list[FeedbackEntry]
    sentiment_counts: dict[str, int] = Field(default_factory=dict)
    pending_sync: int = 0
    taste_profile: TasteProfileResponse


class WeeklyAchievement(BaseModel):
    """阶段5：一周报告中的单类达成率卡片。"""

    key: str = Field(description="nutrition | budget | coverage")
    label: str = Field(description="中文标签，如 '营养达成'")
    percent: float = Field(default=0, description="达成百分比，0-100 区间")
    detail: str = Field(default="", description="一句话补充说明")
    has_data: bool = Field(default=True, description="该指标是否已有可展示的数据")


class CoverageStats(BaseModel):
    """阶段5：计划执行覆盖（餐食打卡 + 采购核销）。"""

    meal_planned: int = 0
    meal_eaten: int = 0
    shopping_planned: int = 0
    shopping_purchased: int = 0
    coverage_percent: float = Field(default=0, description="整体覆盖百分比")


class WeeklySuggestion(BaseModel):
    """阶段5：可操作建议，必须带具体行动而非泛泛之谈。"""

    category: str = Field(description="nutrition | budget | taste | coverage")
    title: str
    detail: str = ""
    action: str = Field(description="具体可执行的行动")


class WeeklyReportResponse(BaseModel):
    """所选自然周的执行报告，默认返回本周。"""

    has_data: bool = Field(default=False, description="所选周是否存在已确认的备餐计划")
    week_start: str = Field(description="报告周期起始日期 ISO 格式")
    week_end: str = Field(description="报告周期结束日期 ISO 格式")
    week_label: str = Field(description="ISO 周标签，如 '2026-W33'")
    achievements: list[WeeklyAchievement] = Field(default_factory=list)
    coverage: CoverageStats = Field(default_factory=CoverageStats)
    suggestions: list[WeeklySuggestion] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class WeeklyReportPeriod(BaseModel):
    """One natural week that has at least one confirmed meal plan."""

    week_start: str
    week_end: str
    week_label: str


class NutritionComparison(BaseModel):
    """餐食替换前后营养对比（单餐或全天）。"""

    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)
    delta: dict[str, float] = Field(default_factory=dict)
    calibrated_before: bool = False
    calibrated_after: bool = False


class DayNutritionComparison(BaseModel):
    """餐食替换前后当天营养合计对比。"""

    day: str
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)
    delta: dict[str, float] = Field(default_factory=dict)


class ShoppingSyncResult(BaseModel):
    """餐食替换触发的购物清单联动结果。"""

    added: list[ShoppingItem] = Field(default_factory=list)
    removed: list[ShoppingItem] = Field(default_factory=list)
    merged_groups: int = 0
    removed_duplicates: int = 0


class MealReplacementResponse(BaseModel):
    """餐食替换结果，同时回传"这次反馈被学到了什么"与营养/清单联动。"""

    meal: MealItem
    feedback: FeedbackSyncInfo | None = None
    taste_profile: TasteProfileResponse
    meal_nutrition: NutritionComparison | None = None
    day_nutrition: DayNutritionComparison | None = None
    shopping_sync: ShoppingSyncResult | None = None


class RecipeInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    ingredients: list[str] = Field(default_factory=list, max_length=100)
    steps: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    allergens: list[str] = Field(default_factory=list, max_length=30)
    duration: int = Field(default=30, ge=1, le=1440)
    estimated_cost: float = Field(default=0, ge=0, le=100000)
    is_favorite: bool = False
    servings: int = Field(default=2, ge=1, le=30)
    nutrition: dict[str, float] = Field(default_factory=dict)


class Recipe(RecipeInput):
    id: int


class RecipeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    ingredients: list[str] | None = Field(default=None, max_length=100)
    steps: list[str] | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=30)
    allergens: list[str] | None = Field(default=None, max_length=30)
    duration: int | None = Field(default=None, ge=1, le=1440)
    estimated_cost: float | None = Field(default=None, ge=0, le=100000)
    is_favorite: bool | None = None
    servings: int | None = Field(default=None, ge=1, le=30)
    nutrition: dict[str, float] | None = None

    @model_validator(mode="after")
    def ensure_values(self) -> "RecipeUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ChatSessionCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=120)


class ChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=2, max_length=4000)
    budget: float = Field(default=500, ge=0, le=100000)


class ChatMessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant", "system"]
    content: str
    run_id: str | None = None
    created_at: datetime


class ChatSessionSummary(BaseModel):
    id: UUID
    title: str
    status: str
    last_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessageResponse]


class BackgroundKnowledgeJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="个人知识", min_length=1, max_length=50)
    content: str = Field(min_length=20, max_length=500000)
    idempotency_key: str | None = Field(default=None, max_length=64)
    priority: str = Field(default="normal", max_length=20)


class BackgroundJobResponse(BaseModel):
    id: UUID
    kind: str
    status: str
    result: dict[str, object]
    error_message: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    idempotency_key: str | None = None
    priority: str = "normal"


class QueueStats(BaseModel):
    """Celery 队列监控指标。"""

    name: str = Field(description="队列名称")
    depth: int = Field(ge=0, description="待处理消息数")
    routing_key: str = Field(default="", description="路由键")


class CeleryStatsResponse(BaseModel):
    """Celery 运行时监控快照。"""

    broker_connected: bool = Field(description="broker 是否可达")
    queues: list[QueueStats] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict, description="后台任务按状态计数")
    recent_jobs: list[BackgroundJobResponse] = Field(default_factory=list)
    dead_letter_count: int = Field(ge=0, description="死信任务数")
    result_expires: int = Field(description="结果过期秒数")
    active_queues: list[str] = Field(default_factory=list)


class DeadLetterItem(BaseModel):
    """死信任务详情。"""

    id: UUID
    kind: str
    error_message: str
    priority: str = "normal"
    created_at: datetime
    finished_at: datetime | None = None


class KnowledgeDocument(BaseModel):
    id: str | int
    name: str
    category: str
    status: str
    chunks: int
    updated_at: date


class KnowledgeTextRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="个人知识", min_length=1, max_length=50)
    content: str = Field(min_length=20, max_length=500_000)
    user_id: int = 1


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    user_id: int = 1
    top_k: int = Field(default=4, ge=1, le=20)


class VectorSearchHit(BaseModel):
    document_id: str
    document_name: str
    category: str
    content: str
    chunk_index: int
    score: float
    # ── 文档 frontmatter 元数据（目标/餐次/过敏/营养侧重），供前端可解释展示 ──
    goal_type: str = "maintain"
    meal_time: str = "通用"
    allergens: str = ""
    nutrition_focus: str = "均衡"


class GraphSearchHit(BaseModel):
    subject: str
    relation: str
    target: str
    detail: str


class RetrievalDiagnostics(BaseModel):
    vector_store: str
    neo4j: str
    embedding: str = "all-MiniLM-L6-v2 (384d)"
    rerank: str = "disabled"


class KnowledgeSearchResponse(BaseModel):
    query: str
    vector_hits: list[VectorSearchHit]
    graph_hits: list[GraphSearchHit]
    elapsed_ms: int
    diagnostics: RetrievalDiagnostics


class AIServiceStatus(BaseModel):
    rag_enabled: bool
    llm_mode: str
    langgraph: str
    vector_store: str
    neo4j: str
    collection: str
    documents: int
    chunks: int
    llm_provider: str
    llm_model: str
    llm_configured: bool
    redis: str = "unknown"
    celery: str = "configured"
    embedding: str = "内置轻量语义模型"
    reranker: str = "未启用二阶段精排"


class SyncConsistencyResponse(BaseModel):
    """向量库与 Neo4j 检索索引的同步一致性快照。"""

    vector_status: str = Field(description="向量知识库连通状态")
    neo4j_status: str = Field(description="关系图谱(Neo4j)连通状态")
    vector_documents: int = Field(description="向量库中文档数量")
    vector_chunks: int = Field(description="向量库中知识片段数量")
    neo4j_documents: int = Field(description="Neo4j 中 Document 节点数量")
    neo4j_entities: int = Field(description="Neo4j 中 KnowledgeEntity 数量")
    missing_in_neo4j: list[str] = Field(
        default_factory=list, description="仅存在于向量库、图谱未同步的文档名"
    )
    orphan_in_neo4j: list[str] = Field(
        default_factory=list, description="仅存在于 Neo4j、向量库缺失的孤儿文档名"
    )
    consistent: bool = Field(description="两份索引是否一致")
    notes: list[str] = Field(default_factory=list, description="一致性说明")


# ---------- Graph RAG 检索质量离线评测 ----------


class RagEvalCase(BaseModel):
    """单条评测用例：自然语言 query 及期望命中的文档/实体类型。"""

    query: str = Field(min_length=2, max_length=1000)
    expected_documents: list[str] = Field(
        default_factory=list, description="期望在 top_k 向量命中中出现的文档名"
    )
    expected_entity_kinds: list[str] = Field(
        default_factory=list, description="期望在图谱命中关系中出现的实体类型"
    )


class RagEvalResult(BaseModel):
    """单条用例的评测结果。"""

    query: str
    recall_at_k: float = Field(ge=0, le=1, description="Recall@k（命中/期望）")
    ndcg_at_k: float = Field(ge=0, le=1, description="nDCG@k 位置折扣增益")
    hit_document_names: list[str] = Field(default_factory=list)
    hit_entity_kinds: list[str] = Field(default_factory=list)


class RagEvalResponse(BaseModel):
    """离线检索质量评测报告。"""

    evaluated_at: str
    embedding: str = Field(description="当前语义向量模型标识")
    reranker: str = Field(description="二阶段精排模型标识")
    top_k: int
    case_count: int
    mean_recall_at_k: float = Field(ge=0, le=1)
    mean_ndcg_at_k: float = Field(ge=0, le=1)
    results: list[RagEvalResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LLMSmokeResponse(BaseModel):
    status: str
    provider: str
    model: str
    latency_ms: int
    message: str


class AgentStep(BaseModel):
    name: str
    label: str
    status: AgentStatus
    duration_ms: int
    summary: str
    output: dict[str, object]


class AgentRun(BaseModel):
    id: UUID
    request: str
    status: AgentStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int
    steps: list[AgentStep]
    error_message: str = ""
    error_type: str = ""
    failed_step: str = ""
    checkpoint: dict[str, object] = Field(default_factory=dict)


class BudgetSummary(BaseModel):
    limit: float
    estimated: float
    saved: float
    usage_percent: int
    categories: dict[str, float]


class BudgetUpdate(BaseModel):
    limit: float | None = Field(default=None, gt=0, le=1000000)
    estimated: float | None = Field(default=None, ge=0, le=1000000)
    categories: dict[str, float] | None = None

    @model_validator(mode="after")
    def ensure_values(self) -> "BudgetUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        if self.categories is not None and any(value < 0 for value in self.categories.values()):
            raise ValueError("预算分类金额不能为负数")
        return self


class Dashboard(BaseModel):
    user_name: str
    greeting: str
    date_label: str
    today_events: list[CalendarEvent] = Field(default_factory=list)
    tasks: list[TaskItem]
    tonight_meal: MealItem
    budget: BudgetSummary
    notices: list[str]
    week_progress: int
    plan_expired: bool = False


class PlanningRequest(BaseModel):
    prompt: str = Field(min_length=5, max_length=1000)
    budget: float = Field(default=500, gt=0, le=100000)
    user_id: int = 1


class ConflictOption(BaseModel):
    """单条降级选项——换菜/换食材/放宽条件等可选动作，供前端渲染与局部重算。

    ``action`` 语义：
      - ``replace_meal``       替换冲突餐食（``proposal`` 为新餐食 dict）
      - ``replace_ingredient`` 替换冲突食材（``proposal`` 为食材名）
      - ``relax_budget``       放宽预算上限
      - ``relax_constraint``   放宽忌口/分类限额等硬约束
    """

    label: str = Field(min_length=1, max_length=120)
    action: Literal["replace_meal", "replace_ingredient", "relax_budget", "relax_constraint"] = (
        "replace_meal"
    )
    proposal: dict[str, Any] | None = Field(
        default=None, description="替换菜/食材提案，供前端渲染与后续局部重算"
    )


class PlanConflict(BaseModel):
    """结构化冲突（校验失败三级策略）。

    ``dimension`` 标识校验维度；``level`` 区分硬冲突（忌口/分类限额，进第 2 级
    降级提示）与软冲突（重复/缺天/营养/预算，优先第 1 级自动修正）。
    ``options`` 为第 2 级降级提示提供的可选项。
    """

    dimension: Literal["allergy", "budget", "coverage", "duplicate", "category_limit", "nutrition"]
    level: Literal["hard", "soft"]
    message: str
    item: str = Field(default="", description="冲突主体（餐食名/维度名）")
    options: list[ConflictOption] = Field(default_factory=list)


class PlanningResponse(BaseModel):
    run_id: UUID
    summary: str
    meals: list[MealItem]
    shopping: list[ShoppingItem]
    tasks: list[TaskItem]
    budget: BudgetSummary
    conflicts: list[str]
    domain: DomainAgentBundle
    sources: list[str]
    trace: list[AgentStep]
    conflict_details: list[PlanConflict] = Field(
        default_factory=list, description="结构化冲突明细（硬/软分级 + 降级选项）"
    )
    auto_fixes: list[str] = Field(
        default_factory=list, description="第 1 级自动修正说明，如 '已自动调整 2 处'"
    )
    needs_manual_review: bool = Field(
        default=False, description="是否触发第 3 级人工接管（硬冲突率>30%）"
    )
    manual_review_hint: str = Field(default="", description="人工接管提示，如 '请放宽条件：……'")


class ChatTurnResponse(BaseModel):
    session: ChatSessionSummary
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    plan: PlanningResponse | None = None


class WeeklyPlanSummary(BaseModel):
    """计划列表视图 — 不含子项详情"""

    id: int
    status: str
    version: int
    is_active: bool
    parent_plan_id: int | None = None
    prompt: str
    budget: float
    summary: str
    created_at: datetime
    meal_count: int = 0
    task_count: int = 0
    shopping_count: int = 0
    is_expired: bool = Field(default=False, description="计划是否已超过 7 天有效期")


class WeekNutrient(BaseModel):
    """单个营养素的周达成进度。"""

    target: float = 0
    consumed: float = 0
    remaining: float = 0
    percent: float = 0


class WeekNutritionResponse(BaseModel):
    """本周营养目标达成进度（按已吃餐食聚合）。"""

    nutrients: dict[str, WeekNutrient] = Field(default_factory=dict)
    overall_percent: float = 0
    eaten_count: int = 0
    total_count: int = 0


class WeeklyPlanDetail(BaseModel):
    """计划详情视图 — 含所有子项"""

    id: int
    status: str
    version: int
    is_active: bool
    parent_plan_id: int | None = None
    prompt: str
    budget: float
    estimated_cost: float = 0
    summary: str
    conflicts: list[str]
    conflict_details: list[PlanConflict] = Field(
        default_factory=list, description="结构化冲突明细（硬/软分级 + 降级选项）"
    )
    auto_fixes: list[str] = Field(
        default_factory=list, description="第 1 级自动修正说明，如 '已自动调整 2 处'"
    )
    needs_manual_review: bool = Field(
        default=False, description="是否触发第 3 级人工接管（硬冲突率>30%）"
    )
    manual_review_hint: str = Field(default="", description="人工接管提示，如 '请放宽条件：……'")
    run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    meals: list[MealItem]
    shopping: list[ShoppingItem]
    tasks: list[TaskItem]
    budget_record: BudgetSummary | None = None
    week_nutrition: WeekNutritionResponse | None = None
    is_expired: bool = Field(default=False, description="计划是否已超过 7 天有效期")


class PlanConfirmationResponse(WeeklyPlanDetail):
    """Confirmed plan payload, retaining the legacy ``plan_id`` field."""

    plan_id: int
    message: str = ""


# ---------- 用户画像与营养目标 ----------


class ActivePlanOverview(BaseModel):
    """Aggregate response consumed by the weekly execution screen."""

    plan: WeeklyPlanDetail | None = None
    versions: list[WeeklyPlanSummary] = Field(default_factory=list)


class UserProfileResponse(BaseModel):
    """用户画像响应：身体数据 + 饮食偏好/忌口 + 预算偏好 + 生活约束。

    ``needs_replan`` 仅在 ``PUT /profile`` 触发规划关键字段变更（goal_type /
    activity_level / constraints / budget_limit 任一变化）时置为 True，提示前端
    "关键字段已变更，是否重新生成下周计划"；``GET /profile`` 恒为 False。
    """

    user_id: int
    height_cm: float
    weight_kg: float
    age: int
    gender: str
    activity_level: str
    goal_type: str
    preferences: list[str]
    constraints: list[str]
    budget_limit: float
    notes: str
    cooking_skill: str
    kitchenware: list[str]
    prep_time_max: int
    needs_replan: bool = False
    profile_complete: bool = False


class UserProfileUpdate(BaseModel):
    """用户画像更新请求，所有字段可选。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    height_cm: float | None = Field(default=None, ge=80, le=250)
    weight_kg: float | None = Field(default=None, ge=20, le=500)
    age: int | None = Field(default=None, ge=1, le=120)
    gender: str | None = Field(default=None, pattern=r"^(male|female)$")
    activity_level: str | None = Field(default=None, pattern=r"^(sedentary|light|moderate|active)$")
    goal_type: str | None = Field(default=None, pattern=r"^(bulk|cut|maintain)$")
    preferences: list[str] | None = Field(default=None, max_length=20)
    constraints: list[str] | None = Field(default=None, max_length=20)
    budget_limit: float | None = Field(default=None, ge=0, le=10000)
    notes: str | None = Field(default=None, max_length=500)
    cooking_skill: str | None = Field(default=None, pattern=r"^(beginner|intermediate|proficient)$")
    kitchenware: list[str] | None = Field(default=None, max_length=20)
    prep_time_max: int | None = Field(default=None, ge=5, le=240)

    @model_validator(mode="after")
    def ensure_update_has_values(self) -> "UserProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class NutritionGoalResponse(BaseModel):
    """营养目标快照响应：BMR/TDEE/目标热量/宏量分配（含范围值与等价物解释）。"""

    user_id: int
    goal_type: str
    bmr: float
    tdee: float
    target_calories: float
    protein_g: float
    carb_g: float
    fat_g: float
    activity_level: str
    calories_min: float
    calories_max: float
    protein_min: float
    protein_max: float
    carb_min: float
    carb_max: float
    fat_min: float
    fat_max: float
    hints: dict[str, str] = Field(
        default_factory=dict,
        description="各营养素的等价物解释文案（如 protein → '相当于 2~3 块鸡胸肉'）",
    )


# ---------- 营养目标求解 ----------


class NutrientEntry(BaseModel):
    """单个营养素的达成情况。"""

    target: float
    actual: float
    percent: float = Field(description="达成百分比，0-200 区间")
    satisfied: bool = Field(description="是否达成目标（>=90% 视为达成）")


class NutritionReport(BaseModel):
    """营养目标求解报告。"""

    targets: dict[str, float] = Field(description="每日营养目标")
    actual: dict[str, float] = Field(description="计划餐食营养合计")
    nutrients: dict[str, NutrientEntry] = Field(default_factory=dict)
    overall_percent: float = Field(ge=0, description="整体达成百分比")
    satisfied: bool = Field(description="整体是否达标")
    calibrated_meals: int = Field(description="命中菜谱的餐数")
    uncalibrated_meals: int = Field(description="未命中菜谱、按食材估算的餐数")
    member_count: int
    meal_count: int


# ---------- 领域智能体评测 ----------


class AgentMetricDetail(BaseModel):
    """单个智能体的评测明细。"""

    score: float = Field(ge=0, le=100, description="0-100 分")
    metrics: dict[str, Any] = Field(default_factory=dict, description="原始指标键值")
    issues: list[str] = Field(default_factory=list, description="扣分原因")


class AgentEvaluation(BaseModel):
    """领域智能体评测结果。"""

    overall_score: float = Field(ge=0, le=100, description="加权综合评分")
    scores: dict[str, float] = Field(
        default_factory=dict, description="各智能体评分: meal/shopping/task/budget"
    )
    details: dict[str, AgentMetricDetail] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list, description="全局问题清单")
    prompt_versions: dict[str, str] = Field(
        default_factory=dict, description="评测时各智能体所用提示词版本"
    )


# ---------- 提示词版本管理 ----------


class PromptVersionInfo(BaseModel):
    """提示词版本信息。"""

    name: str
    version: str
    system_message: str
    instruction: str
    changelog: str = ""
    released_at: str = "2026-08-05"
    is_active: bool = False


class PromptRegistryResponse(BaseModel):
    """提示词注册表响应。"""

    agents: dict[str, list[PromptVersionInfo]]
    active_versions: dict[str, str]


# ---------- 库存管理 ----------


class InventoryEntry(BaseModel):
    """库存项视图。"""

    id: int
    name: str
    category: str
    quantity: str
    quantity_value: float
    unit: str
    low_stock_threshold: float
    note: str = ""
    is_low_stock: bool = False


class InventoryAdjustRequest(BaseModel):
    """库存调整请求：delta 为正入库、为负出库。"""

    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="未分类", min_length=1, max_length=40)
    delta: float = Field(description="正数入库，负数出库")
    unit: str = Field(default="个", min_length=1, max_length=20)
    quantity: str | None = Field(default=None, max_length=40, description="可选显示数量")
    low_stock_threshold: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=500)


class InventoryResponse(BaseModel):
    """库存列表响应，含低库存预警。"""

    items: list[InventoryEntry]
    count: int
    low_stock_count: int


# ---------- 计划归档 ----------


class ArchivedPlanResponse(BaseModel):
    """归档操作响应。"""

    id: int
    status: str
    is_active: bool
    archived_at: datetime


# ---------- G08 购物替代图谱化 ----------


class SubstitutionSuggestion(BaseModel):
    """单条食材替代建议。

    ``source`` 标识数据来源：``graph`` 为 Neo4j 显式 SUBSTITUTABLE_FOR 关系，
    ``nutrition`` 为食材营养库余弦相似度兜底。前端可据此区分"权威替代"
    与"营养近似"。
    """

    name: str = Field(min_length=1, max_length=120)
    reason: str = ""
    similarity: float = Field(ge=0.0, le=1.0)
    source: str = Field(default="graph", pattern="^(graph|nutrition)$")
    nutrition: dict[str, float] | None = Field(
        default=None,
        description="替代食材每 100g 营养快照，用于前端对比展示",
    )


class ShoppingSubstitutionResponse(BaseModel):
    """购物替代建议响应。"""

    item_id: int
    name: str
    suggestions: list[SubstitutionSuggestion]
    source_summary: dict[str, int] = Field(
        default_factory=dict,
        description="按来源统计的命中数，如 {'graph': 2, 'nutrition': 3}",
    )


class ShoppingSubstitutionDecision(BaseModel):
    """购物项替换确认请求（接受 / 拒绝 / 换一个）。

    - ``accept``：确认当前替换，``substituted_accepted=True``。
    - ``reject``：拒绝替换，回退到 ``substituted_from`` 并清空替换标记。
    - ``swap``：换一个替代品；``name`` 传前端选定的新食材名，缺省时自动召回
      与当前名称不同的下一条替代建议。
    """

    action: Literal["accept", "reject", "swap"] = "accept"
    name: str | None = Field(default=None, min_length=1, max_length=120)


class SubstitutionSeedResponse(BaseModel):
    """替代关系图谱种子同步响应。"""

    seeded_edges: int = Field(ge=0, description="成功写入的替代边数（双向计数）")
    total_pairs: int = Field(ge=0, description="种子数据中的替代对总数")
    note: str = ""


# ---------- 菜谱首页目录 ----------


class RecipeCategory(StrEnum):
    FAT_LOSS = "fat_loss"
    MUSCLE_GAIN = "muscle_gain"
    HEALTHY = "healthy"


class RecipeIngredient(BaseModel):
    name: str
    amount: str


class RecipeNutrition(BaseModel):
    calories: float
    protein: float
    carbs: float
    fat: float


class RecipeSummary(BaseModel):
    id: str
    name: str
    category: RecipeCategory
    description: str
    image_url: str = ""
    emoji: str = "🍽️"
    gradient: str = "linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
    calories: int
    prep_time: int
    difficulty: str
    servings: int
    rating: float = 0.0
    tags: list[str] = []


class RecipeDetail(RecipeSummary):
    ingredients: list[RecipeIngredient] = []
    steps: list[str] = []
    nutrition: RecipeNutrition


class RecipeListResponse(BaseModel):
    recipes: list[RecipeSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


class RecipeTipsResponse(BaseModel):
    """菜谱详情里的营养师小贴士（§8：LLM 生成 + 缓存，当前为确定性生成）。"""

    recipe_id: str
    tip: str

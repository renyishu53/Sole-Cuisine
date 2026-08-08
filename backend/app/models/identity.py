"""SoloChef 数据模型（去家庭化版）。

定位收敛（2026-08-07）：原 CasaMind 家庭导向模型已去家庭化，所有业务表以
``user_id`` 为核心；``families / family_memberships / family_member_profiles /
family_invitations / event_participants`` 已删除；新增 ``UserProfile``（身体数据
+ 饮食偏好）与 ``NutritionGoal``（TDEE + 宏量分配）支撑营养目标驱动生成。

Phase 3 清理（2026-08-09）：``calendar_events / calendar_event_exceptions /
plan_tasks / plan_budgets / task_completions / inventory_items`` 6 张遗留表
及对应模型已移除，数据模型精简为 14 张核心表。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("phone", name="users_phone_key"),
        Index("ix_users_phone", "phone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    token_version: Mapped[int] = mapped_column(default=1, server_default="1")

    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    nutrition_goal: Mapped["NutritionGoal | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserProfile(TimestampMixin, Base):
    """独居用户画像：身体数据 + 饮食偏好/忌口 + 预算偏好。

    身体数据用于计算 TDEE 与宏量分配（见 :class:`NutritionGoal`）。
    """

    __tablename__ = "user_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_profile_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 身体数据
    height_cm: Mapped[float] = mapped_column(Float, default=170.0, server_default="170")
    weight_kg: Mapped[float] = mapped_column(Float, default=65.0, server_default="65")
    age: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    gender: Mapped[str] = mapped_column(String(10), default="male", server_default="male")
    activity_level: Mapped[str] = mapped_column(
        String(20), default="moderate", server_default="moderate"
    )
    # 饮食偏好与限制
    goal_type: Mapped[str] = mapped_column(
        String(20), default="maintain", server_default="maintain"
    )
    preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget_limit: Mapped[float] = mapped_column(Float, default=500, server_default="500")
    notes: Mapped[str] = mapped_column(String(500), default="", server_default="")

    user: Mapped[User] = relationship(back_populates="profile")


class NutritionGoal(TimestampMixin, Base):
    """营养目标快照：由身体数据按 Mifflin-St Jeor 公式计算并保存。

    增肌/减脂/维护对应不同热量与宏量分配；性别差异体现在基础代谢。
    """

    __tablename__ = "nutrition_goals"
    __table_args__ = (UniqueConstraint("user_id", name="uq_nutrition_goal_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    goal_type: Mapped[str] = mapped_column(
        String(20), default="maintain", server_default="maintain"
    )
    bmr: Mapped[float] = mapped_column(Float, default=1500, server_default="1500")
    tdee: Mapped[float] = mapped_column(Float, default=2000, server_default="2000")
    target_calories: Mapped[float] = mapped_column(Float, default=2000, server_default="2000")
    protein_g: Mapped[float] = mapped_column(Float, default=120, server_default="120")
    carb_g: Mapped[float] = mapped_column(Float, default=220, server_default="220")
    fat_g: Mapped[float] = mapped_column(Float, default=60, server_default="60")
    activity_level: Mapped[str] = mapped_column(
        String(20), default="moderate", server_default="moderate"
    )

    user: Mapped[User] = relationship(back_populates="nutrition_goal")


class AgentRunRecord(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="completed")
    prompt: Mapped[str] = mapped_column(String(1000))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    llm_mode: Mapped[str] = mapped_column(String(30), default="demo", server_default="demo")
    summary: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    error_message: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    error_type: Mapped[str] = mapped_column(String(120), default="", server_default="")
    failed_step: Mapped[str] = mapped_column(String(120), default="", server_default="")
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plans: Mapped[list["WeeklyPlan"]] = relationship(back_populates="agent_run")


class WeeklyPlan(TimestampMixin, Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "run_id", name="uq_weekly_plan_user_run"),
        UniqueConstraint("user_id", "version", name="uq_weekly_plan_user_version"),
        # 单个活跃计划由应用层保证（去家庭化后不再用部分索引，便于 MySQL 兼容）
        Index("ix_weekly_plan_user_active", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    parent_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(String(1000))
    budget: Mapped[float] = mapped_column(default=500, server_default="500")
    summary: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    conflicts: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)

    user: Mapped[User] = relationship()
    agent_run: Mapped[AgentRunRecord | None] = relationship(back_populates="plans")
    parent_plan: Mapped["WeeklyPlan | None"] = relationship(
        remote_side="WeeklyPlan.id",
        back_populates="revisions",
        foreign_keys=[parent_plan_id],
    )
    revisions: Mapped[list["WeeklyPlan"]] = relationship(
        back_populates="parent_plan",
        foreign_keys=[parent_plan_id],
    )
    meals: Mapped[list["PlanMealItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    shopping_items: Mapped[list["PlanShoppingItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanMealItem(TimestampMixin, Base):
    __tablename__ = "plan_meal_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    duration: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    cost: Mapped[float] = mapped_column(default=0, server_default="0")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(String(500), default="", server_default="")
    ingredients: Mapped[list[str]] = mapped_column(JSON, default=list)

    plan: Mapped[WeeklyPlan] = relationship(back_populates="meals")


class PlanShoppingItem(TimestampMixin, Base):
    __tablename__ = "plan_shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="未分类", server_default="未分类")
    quantity: Mapped[str] = mapped_column(String(40), default="1", server_default="1")
    price: Mapped[float] = mapped_column(default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(100), default="", server_default="")
    purchased: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    plan: Mapped[WeeklyPlan] = relationship(back_populates="shopping_items")


class ExpenseRecord(TimestampMixin, Base):
    __tablename__ = "expense_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    shopping_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_shopping_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), default="其他", server_default="其他")
    amount: Mapped[float] = mapped_column(default=0, server_default="0")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class RecipeRecord(TimestampMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_recipe_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    ingredients: Mapped[list[str]] = mapped_column(JSON, default=list)
    steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    estimated_cost: Mapped[float] = mapped_column(default=0, server_default="0")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    servings: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    nutrition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class PlanFeedback(TimestampMixin, Base):
    """执行反馈偏差表（去家庭化版，以 user_id 为核心）。

    任务完成 / 餐食替换 / 购物核销 / 支出录入的执行结果统一落到这里，既记录
    主观反馈（``sentiment`` / ``content`` / ``rating``），也记录客观偏差
    （``planned_value`` / ``actual_value`` / ``deviation``）。写入后由
    :mod:`app.services.feedback_loop` 回流到 Neo4j 与 Chroma，形成
    "计划 → 执行 → 反馈 → 检索/记忆" 的闭环。
    """

    __tablename__ = "plan_feedback"
    __table_args__ = (
        Index("ix_plan_feedback_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    feedback_type: Mapped[str] = mapped_column(String(40), index=True)
    reference_type: Mapped[str] = mapped_column(String(40), default="", server_default="")
    reference_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    subject: Mapped[str] = mapped_column(String(160), default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment: Mapped[str] = mapped_column(String(20), default="neutral", server_default="neutral")
    content: Mapped[str] = mapped_column(Text, default="")
    planned_value: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    actual_value: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    deviation: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    synced_to_graph: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    synced_to_vector: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )


class ChatSession(TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="active", server_default="active")
    last_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(TimestampMixin, Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class BackgroundJob(TimestampMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        Index("ix_background_jobs_idem", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="queued", server_default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), default=None)
    priority: Mapped[str] = mapped_column(
        String(20), default="normal", server_default="normal"
    )


class RefreshSession(TimestampMixin, Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

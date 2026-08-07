# CasaMind 阶段1：计划与 Agent Run 持久化 实施计划

## 背景

当前最高优先级任务是**计划与 Agent Run 持久化**。当前问题：

1. Agent Run 存储在 `PlanningService._runs` 进程内存中，服务重启后丢失
2. `agent_runs` 表（[identity.py#L165](file:///d:/pyton_feisi/project/project_agent/backend/app/models/identity.py#L165)）已存在但未被写入
3. `confirm_plan`（[router.py#L462](file:///d:/pyton_feisi/project/project_agent/backend/app/api/router.py#L462)）只返回成功消息，无事务写入
4. Meals/Shopping/Tasks/Budget 端点返回硬编码 demo data（[demo_data.py](file:///d:/pyton_feisi/project/project_agent/backend/app/services/demo_data.py)）
5. 前端任务勾选、购物勾选只修改本地状态

> **命名约定**：`run_id` = UUID 字符串（Agent Run 的 ID），`plan_id` = 整数（DB 中 WeeklyPlan 的主键）。`POST /plans/{run_id}/confirm` 接收的是 UUID 类型的 run_id。

## 实施步骤

### 步骤1：更新 ORM 模型

**文件：** `backend/app/models/identity.py`

**1.1 增强 `AgentRunRecord`（在现有字段后追加）：**

```python
# 新增字段（追加到 Line 175 之后）
duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
sources: Mapped[list[str]] = mapped_column(JSON, default=list)
llm_mode: Mapped[str] = mapped_column(String(30), default="demo", server_default="demo")
summary: Mapped[str] = mapped_column(String(2000), default="", server_default="")

# 新增反向关系（追加到所有字段之后）
plans: Mapped[list["WeeklyPlan"]] = relationship(back_populates="agent_run")
```

> **注意**：不添加 `started_at`。`TimestampMixin.created_at` 在 Run 创建时即为工作流开始时间（因为在 workflow 开始时就创建 DB 记录），Schema 映射时用 `created_at` 作为 `started_at`。

**1.2 在 `AgentRunRecord` 类之后新增 5 个模型类：**

```python
class WeeklyPlan(TimestampMixin, Base):
    __tablename__ = "weekly_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    prompt: Mapped[str] = mapped_column(String(1000))
    budget: Mapped[float] = mapped_column(default=500, server_default="500")
    summary: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    conflicts: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)

    family: Mapped[Family] = relationship(back_populates="plans")
    user: Mapped[User] = relationship()
    agent_run: Mapped[AgentRunRecord | None] = relationship(back_populates="plans")
    meals: Mapped[list["PlanMealItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    shopping_items: Mapped[list["PlanShoppingItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["PlanTask"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    budget_record: Mapped["PlanBudget | None"] = relationship(
        back_populates="plan", cascade="all, delete-orphan", uselist=False
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
    purchased: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    plan: Mapped[WeeklyPlan] = relationship(back_populates="shopping_items")


class PlanTask(TimestampMixin, Base):
    __tablename__ = "plan_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120))
    assignee: Mapped[str] = mapped_column(String(80), default="", server_default="")
    duration: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    due: Mapped[str] = mapped_column(String(50), default="", server_default="")
    status: Mapped[str] = mapped_column(String(30), default="todo", server_default="todo")
    category: Mapped[str] = mapped_column(String(40), default="未分类", server_default="未分类")

    plan: Mapped[WeeklyPlan] = relationship(back_populates="tasks")


class PlanBudget(TimestampMixin, Base):
    __tablename__ = "plan_budgets"
    __table_args__ = (UniqueConstraint("plan_id", name="uq_plan_budget"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    limit: Mapped[float] = mapped_column(default=500, server_default="500")
    estimated: Mapped[float] = mapped_column(default=0, server_default="0")
    saved: Mapped[float] = mapped_column(default=0, server_default="0")
    usage_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    categories: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    plan: Mapped[WeeklyPlan] = relationship(back_populates="budget_record")
```

**1.3 在 `Family` 类中添加反向关系（追加到 Line 54 之后）：**

```python
    plans: Mapped[list["WeeklyPlan"]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )
```

**文件：** `backend/app/models/__init__.py` — 导出新模型

### 步骤2：创建 Alembic 迁移

**文件：** `backend/alembic/versions/20260804_01_plan_persistence.py`

- `down_revision = "20260803_03"`
- `upgrade()`：
  - ALTER TABLE `agent_runs` ADD COLUMN `duration_ms`, `steps`, `sources`, `llm_mode`, `summary`
  - CREATE TABLE `weekly_plans`, `plan_meal_items`, `plan_shopping_items`, `plan_tasks`, `plan_budgets` 及对应索引
- `downgrade()`：DROP 新表，ALTER TABLE `agent_runs` DROP 新列

### 步骤3：新增 Pydantic Schemas

**文件：** `backend/app/schemas/domain.py`（追加到现有文件末尾）

```python
class WeeklyPlanSummary(BaseModel):
    """计划列表视图"""
    id: int
    status: str
    version: int
    prompt: str
    budget: float
    summary: str
    created_at: datetime
    meal_count: int = 0
    task_count: int = 0
    shopping_count: int = 0


class WeeklyPlanDetail(BaseModel):
    """计划详情视图"""
    id: int
    status: str
    version: int
    prompt: str
    budget: float
    summary: str
    conflicts: list[str]
    suggestions: list[str]
    run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    meals: list[MealItem]
    shopping: list[ShoppingItem]
    tasks: list[HouseholdTask]
    budget_record: BudgetSummary | None = None
```

更新 `PlanningRequest`：添加 `user_id: int = 1` 字段

**文件：** `backend/app/schemas/__init__.py` — 导出新 schema

### 步骤4：新建 PlanningRepository

**文件：** `backend/app/repositories/planning.py`（新建）

遵循 `CalendarRepository`（[calendar.py](file:///d:/pyton_feisi/project/project_agent/backend/app/repositories/calendar.py)）的完整模式：`__init__(self, session: AsyncSession)`，全部异步方法，family_id 隔离。

```python
class PlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Agent Run ---
    async def create_agent_run(self, run_id, family_id, user_id, prompt, status="running") -> AgentRunRecord
    async def update_agent_run(self, run_id, family_id, **values) -> AgentRunRecord | None
    async def get_agent_run(self, run_id, family_id) -> AgentRunRecord | None
    async def list_agent_runs(self, family_id, limit=20) -> list[AgentRunRecord]

    # --- Plan ---
    async def create_plan(self, family_id, user_id, **values) -> WeeklyPlan
    async def confirm_plan(self, plan, meals, shopping, tasks, budget) -> WeeklyPlan  # 事务写入
    async def get_plan(self, plan_id, family_id) -> WeeklyPlan | None  # selectinload 子项
    async def get_plan_by_run_id(self, run_id, family_id) -> WeeklyPlan | None
    async def list_plans(self, family_id, limit=20) -> list[WeeklyPlan]
    async def get_active_plan(self, family_id) -> WeeklyPlan | None  # 最近 confirmed 计划

    # --- 单项更新 ---
    async def get_shopping_item(self, item_id, family_id) -> PlanShoppingItem | None
    async def get_task(self, task_id, family_id) -> PlanTask | None
```

**文件：** `backend/app/repositories/__init__.py` — 导出 `PlanningRepository`

### 步骤5：更新 PlanningService

**文件：** `backend/app/services/planning.py`

核心变更：
- **移除 `self._runs` 内存字典**
- `generate()` 新增可选 `session: AsyncSession | None` 参数，提供时：
  - 在调用 workflow 前创建 AgentRunRecord（status="running"）
  - 在 workflow 完成后更新记录（status="completed", duration_ms, steps, sources, summary, llm_mode, payload）
- `get_run()` 改为从 DB 读取
- 保持无状态单例 `planning_service = PlanningService()`

### 步骤6：更新 API 路由

**文件：** `backend/app/api/router.py`

**更新端点：**
- `POST /plans/generate-weekly`（Line 450）：传入 `user_id` 和 `session` 给 planning_service
- `POST /plans/{run_id}/confirm`（Line 462）：接收 UUID 类型的 run_id，重写为 DB 事务写入 + 幂等检查
- `GET /agents/runs/{run_id}`（Line 470）：从 DB 读取
- `GET /meals`（Line 347）：从活跃计划读取，fallback demo data
- `GET /shopping`（Line 352）：同上
- `GET /tasks`（Line 342）：同上

**新增端点：**
- `GET /plans`：列出当前家庭计划列表（返回 `list[WeeklyPlanSummary]`）
- `GET /plans/{plan_id}`：获取计划详情（接收 int 类型 plan_id，返回 `WeeklyPlanDetail`）
- `GET /agents/runs`：列出 Agent Run 历史
- `PATCH /shopping/{item_id}`：更新购物项 purchased 状态
- `PATCH /tasks/{task_id}`：更新任务 status

**新增辅助函数：**
- `_agent_run_to_schema(record)`：ORM AgentRunRecord → AgentRun schema（`created_at` 映射到 `started_at`）
- `_plan_to_detail(plan)`：ORM WeeklyPlan → WeeklyPlanDetail schema

### 步骤7：前端类型定义

**文件：** `frontend/src/types.ts`

新增 `WeeklyPlanSummary`, `WeeklyPlanDetail` 类型定义。

### 步骤8：前端 API 层

**文件：** `frontend/src/api.ts`

新增方法：`listPlans()`, `getPlan(id)`, `listAgentRuns()`, `updateShoppingItem(id, body)`, `updateTask(id, body)`

### 步骤9：前端视图更新

**策略：乐观更新 + 失败回滚。** 先修改本地状态获得即时响应，再调用 API。如果 API 失败，回滚到原始状态并显示错误提示。这与现有 `PlannerView.vue` 中 `savePlan()` 的 `try/catch` + loading 模式一致。

**文件：** `frontend/src/views/PlannerView.vue`
- `savePlan()` 保存成功后存储 `plan_id`
- 下方新增"历史计划"列表区域

**文件：** `frontend/src/views/AgentView.vue`
- `onMounted` 中无 `lastRunId` 时调用 `listAgentRuns` 获取最近记录
- 新增历史 Run 下拉选择器

**文件：** `frontend/src/views/ShoppingView.vue`
- `purchased` 勾选后：先改本地 `item.purchased`，再调 API；失败则回滚

**文件：** `frontend/src/views/TasksView.vue`
- `advance()` 先改本地状态，再调 API；失败则回滚

**文件：** `frontend/src/views/MealsView.vue`, `BudgetView.vue`
- 数据流不变（后端已更新），无需修改

### 步骤10：测试

**文件：** `backend/tests/test_api.py`

新增测试用例：
- `test_agent_run_persisted_to_database`：生成计划后从 DB 读取 run，重启后数据仍存在
- `test_plan_confirmation_writes_to_database`：确认后 meals/shopping/tasks 端点返回持久化数据
- `test_confirm_plan_is_idempotent`：重复确认返回相同 plan_id
- `test_plan_cross_family_isolation`：跨家庭 404

## 验证方式

1. `uv run alembic upgrade head` 执行迁移
2. `uv run pytest -q` 确保所有测试通过
3. 启动后端，通过 Yaak 测试各端点
4. 重启后端，确认 Agent Run 历史数据持久化
5. 启动前端，验证 Planner 历史计划、Agent Run 历史、Shopping/Tasks 状态持久化
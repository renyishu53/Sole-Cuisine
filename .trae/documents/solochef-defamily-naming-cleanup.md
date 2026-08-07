# SoloChef 去 Family 命名收尾 — 实施计划

## Context（背景与目标）

项目正从 CasaMind/HomePilot（家庭综合事务规划）收敛为 **SoloChef**（独居营养备餐助手）。后端去家庭化的"机械替换"已完成（模型 `family_id → user_id`、家庭表删除、`UserProfile`/`NutritionGoal` 新增、MySQL 适配），但**命名层与语义层仍有大量家庭遗留**，与分析报告 〇.7、PRD 第 10 节"仍需收敛"一致：

- `HouseholdPlanningWorkflow`、`HouseholdRecipe`、`HouseholdTask`、`FamilyMember` 等类名仍偏家庭
- `casamind` Celery 主名 / 任务名 / `casamind_token_sink` / `CASAMIND_LLM_OK` / `casamind-api` 等品牌串
- LLM 提示词、错误提示、docstring 中 "CasaMind" / "家庭" 字样
- 种子知识文档标题（"控糖家庭饮食原则.md"）、rag_eval 期望文档名、demo_data 示例数据
- 测试文件仍是完整家庭导向：`test_api.py` 大量调用**已删除**的 `/api/v1/families`、`/invitations`、`/memberships` 端点（已核实 router.py / auth_router.py 无这些端点 → 这些是既有失效测试）

**目标**：完成后端代码层 + 种子/评测数据 + 测试层的彻底去家庭化命名收敛，对齐 SoloChef 定位。用户已确认范围：test_api.py 失效端点测试**重写为 SoloChef 等价测试**；字符串收敛**包含种子/评测数据**。

**约束**：遵循现有代码风格（SQLAlchemy 2.x `Mapped`、Pydantic v2、中文 docstring、`server_default` 兼容 MySQL、Repository 带 `user_id` 隔离 + `selectinload`、可选依赖优雅降级）。不改业务行为，仅命名/语义收敛 + 测试等价改写。

---

## 重命名映射表（核心契约）

### 标识符（类 / 函数 / 变量 / 表）

| 旧名 | 新名 | 理由 |
|---|---|---|
| `HouseholdPlanningWorkflow` | `SoloChefWorkflow` | 品牌对齐 |
| `HouseholdRecipe`（模型类） | `RecipeRecord` | 与 `CalendarEventRecord`/`AgentRunRecord`/`ExpenseRecord` 一致 |
| `household_recipes`（表名） | `recipes` | 干净表名 |
| `HouseholdTask` / `Create` / `Update`（schema） | `TaskItem` / `TaskItemCreate` / `TaskItemUpdate` | 与 `MealItem`/`ShoppingItem` 命名一致 |
| `FamilyMember` / `Create` / `Update`（schema） | `MemberProfile` / `MemberProfileCreate` / `MemberProfileUpdate` | 去 "Family"；该 schema 表示规划成员画像 |
| `sync_family_graph`（worker 函数） | `sync_user_graph` | 已调用 `sync_user_context`，名字对齐 |
| `casamind`（Celery 主名） | `solochef` | 品牌 |
| `casamind.process_knowledge_text/file` | `solochef.process_knowledge_text/file` | 品牌 |
| `casamind.sync_family_graph`（任务名） | `solochef.sync_user_graph` | 品牌 + user |
| `casamind.cleanup_old_jobs` | `solochef.cleanup_old_jobs` | 品牌 |
| `casamind_token_sink`（ContextVar） | `solochef_token_sink` | 品牌 |
| `CASAMIND_LLM_OK`（冒烟哨兵串） | `SOLOCHEF_LLM_OK` | 品牌 |

### 字符串（CasaMind→SoloChef；家庭→用户/个人）

- **LLM 提示词** `app/ai/prompts.py`：9 处 "你是 CasaMind 的..." → "你是 SoloChef 的..."；meal prompt "家庭历史反馈/家庭偏好" → "用户历史反馈/用户偏好"
- **LLM 生成** `app/ai/llm.py`："CasaMind 家庭规划协调智能体" → "SoloChef 个人规划协调智能体"；demo summary "已结合家庭成员画像..." → "已结合用户画像..."；"建议由可用成员接送" → "建议调整接送安排"
- **工作流** `app/ai/workflow.py`：`"weekly_family_plan"` → `"weekly_user_plan"`；"识别家庭规划意图" → "识别用户规划意图"；"家庭知识图谱" → "用户知识图谱"；"CasaMind · 无外部检索上下文" → "SoloChef · 无外部检索上下文"；"菜单命中家庭忌口" → "菜单命中用户忌口"；"家务计划" → "任务计划"
- **RAG 服务** `query_rewriter.py` / `entity_extractor.py`："CasaMind 家庭知识图谱..." → "SoloChef 用户知识图谱..."
- **DB base** `app/db/base.py`：docstring "CasaMind relational models" → "SoloChef relational models"
- **路由** `app/api/router.py`：`"casamind-api"` → `"solochef-api"`；"家庭任务不存在" → "任务不存在"；"家庭库存列表/家庭采购历史/家庭的死信任务列表/家庭场景评测集" → 去"家庭"；"家庭知识"默认 → "个人知识"；"当前家庭日程..." → "当前日程..."；"计划已保存到家庭空间" → "计划已保存"
- **Repository** `domain.py` / `feedback.py`：docstring "当前家庭/家庭支出历史/家庭库存/家庭归属/家庭口味画像" → 去"家庭"（"用户"或直述）
- **feedback_loop.py** docstring：`(:Family)-[:HAS_FEEDBACK]` → `(:User)-[:HAS_FEEDBACK]`（与 graph_store 实际 `:User` 对齐）
- **保留**：`graph_store.py` / `repositories/*.py` / `auth.py` 中"去家庭化版"说明性 docstring（解释迁移历史，有价值）

### 种子 / 评测 / 示例数据（协调重命名）

- `app/services/knowledge.py` 种子文档：
  - "家庭晚餐优先蒸、煮、烩..." → "独居备餐优先蒸、煮、烩..."
  - "控糖家庭饮食原则.md" / "# 控糖家庭饮食原则" → "控糖饮食原则.md" / "# 控糖饮食原则"
  - "家庭成员有少糖约束时..." → "用户有少糖约束时..."
  - "家庭任务公平分配.md" / "# 家庭任务公平分配" → "任务安排原则.md" / "# 任务安排原则"（单用户任务调度语义）
- `app/services/rag_eval.py` 期望文档名与 query 同步：`"控糖家庭饮食原则"` → `"控糖饮食原则"`；`"家庭任务公平分配"` → `"任务安排原则"`；query "控糖家庭的饮食原则" → "控糖饮食原则"；"家庭任务如何公平分配" → "任务如何安排"
- `app/services/demo_data.py`：`category="家庭"` → `"个人"`；`title="家庭周计划"` → `"周计划"`；`member="全家"` → 单用户名；`tags=["清淡","家庭餐"]` → `["清淡","日常"]`
- `app/schemas/domain.py`：`CalendarEventCreate.category` 默认 `"family"` → `"personal"`；`BackgroundKnowledgeJobCreate.category` 默认 `"家庭知识"` → `"个人知识"`；`CeleryStatsResponse.status_counts` 描述 "家庭后台任务" → "后台任务"；`NutritionReport` docstring "家庭营养目标求解报告" → "营养目标求解报告"，`targets` 描述 "家庭每日营养目标" → "每日营养目标"

---

## 分阶段执行

### Phase A — 代码层命名收敛（核心，机械替换）

按依赖顺序自底向上改，避免中间态导入失败：

1. **模型层** `app/models/identity.py` + `app/models/__init__.py`：`HouseholdRecipe` → `RecipeRecord`，`__tablename__` `household_recipes` → `recipes`
2. **Schema 层** `app/schemas/domain.py` + `app/schemas/__init__.py`：`FamilyMember*` → `MemberProfile*`、`HouseholdTask*` → `TaskItem*`，更新 `Dashboard`/`PlanningResponse`/`WeeklyPlanDetail`/`TaskAutoAssignResponse` 中的 `list[HouseholdTask]` → `list[TaskItem]`，更新默认串与 docstring
3. **AI 层** `app/ai/workflow.py`（`HouseholdPlanningWorkflow` → `SoloChefWorkflow`，`FamilyMember` → `MemberProfile`，trace 文案）、`app/ai/prompts.py`（CasaMind→SoloChef）、`app/ai/domain_agents.py`（`FamilyMember` → `MemberProfile`，"家庭友好" → "日常友好"）、`app/ai/llm.py`（`HouseholdTask` → `TaskItem`，CasaMind 串，ContextVar 名，哨兵串）
4. **Repository 层** `app/repositories/domain.py`、`feedback.py`、`planning.py`：`HouseholdRecipe` → `RecipeRecord`，`HouseholdTask` → `TaskItem`，docstring 去"家庭"
5. **Service 层** `app/services/domain.py`、`nutrition.py`、`planning.py`（`HouseholdPlanningWorkflow` → `SoloChefWorkflow`，`FamilyMember` → `MemberProfile`）、`calendar_planning.py`（`FamilyMember` → `MemberProfile`）、`demo_data.py`（`HouseholdTask` → `TaskItem` + 示例数据）、`feedback_loop.py`（docstring `:Family` → `:User`）、`query_rewriter.py`/`entity_extractor.py`（CasaMind 串）
6. **API 层** `app/api/router.py`：导入与类型注解 `HouseholdRecipe`/`HouseholdTask*` → `RecipeRecord`/`TaskItem*`；`_recipe_response(recipe: HouseholdRecipe)` → `RecipeRecord`；`_task_response` 返回 `TaskItem`；错误串与 `casamind-api`、`casamind.*` 任务名 → `solochef.*`
7. **Worker** `app/worker.py`：`Celery("casamind")` → `Celery("solochef")`；`task_routes` / `name=` / `beat_schedule` 全部 `casamind.*` → `solochef.*`；`sync_family_graph`/`_sync_family_graph` → `sync_user_graph`/`_sync_user_graph`
8. **DB base** `app/db/base.py`：docstring CasaMind → SoloChef

> Phase A 改动遵循"最小编辑"原则，每处仅改名/改串，不动逻辑分支与参数顺序。

### Phase B — 种子/评测数据协调重命名

按 Phase A 后执行（依赖 `RecipeRecord` 等已就位）：

1. `app/services/knowledge.py`：重命名 3 个种子文档标题与内容（见映射表）
2. `app/services/rag_eval.py`：评测集 query 与 `expected_documents` 同步重命名
3. `app/services/demo_data.py`：示例数据 `家庭` → SoloChef 语义

### Phase C — 测试层收敛

1. **`tests/conftest.py`**：`HouseholdPlanningWorkflow` → `SoloChefWorkflow`；注册 fixture 去掉 `family_name`，改用 SoloChef 单用户注册流程；`"casamind-test"` 密码可保留（仅测试密钥）或改 `solochef-test`
2. **`tests/test_rag.py`**：`HouseholdPlanningWorkflow` → `SoloChefWorkflow`（5 处）；`FamilyMember` → `MemberProfile`（3 处）；`FakeKnowledgeService` 签名 `family_id` → `user_id`（对齐 workflow `KnowledgeRetriever` 协议）；`casamind_knowledge` → `solochef_knowledge`（3 处）；测试数据 "家庭晚餐/家庭体检/家庭计划" → SoloChef 语义
3. **`tests/test_graph_rag_quality.py`**：`search(self, query, family_id, top_k)` → `user_id`；"控糖家庭饮食原则" → "控糖饮食原则"
4. **`tests/test_api.py` 重写**（最大工作量，按测试函数逐个映射家庭关注点 → SoloChef 等价关注点）：
   - 家庭创建/列表测试 → 删除，替换为 `/auth/register` + `/auth/me` 单用户注册验证
   - `test_member_profile_crud_is_family_scoped` → 删除（无 /members 端点）；可替换为 `/recipes` CRUD user-scope 验证
   - 邀请 / 成员关系测试 → 删除（无对应端点）；可补充 `/auth/sms/login` 自动注册测试
   - `test_*_cross_family_isolation` / `*_family_scoped` → `*_cross_user_isolation` / `*_user_scoped`：注册两个用户，断言 A 看不到 B 的日历/任务/菜谱/对话/计划/任务数据（这是 SoloChef 的核心隔离保证）
   - 计划/日历/餐食/购物/知识/后台任务测试：去掉创建家庭的 setup，直接用已注册用户的 `auth_headers`；`"家庭知识"` category → `"个人知识"`；`casamind.*` 任务名断言 → `solochef.*`；prompt 中"家庭计划" → "一周计划"
   - 设备会话 / 找回密码测试：去 `family_name`，验证 `/auth/sessions` 与 `/auth/password/reset`

### Phase D — 验证

1. `python -c "from app.main import app"` — 导入冒烟（93+12 路由）
2. `python -m py_compile` 全部改动文件
3. `ruff check app tests alembic` — 无新增 lint 错误
4. `mypy app tests` — 无新增类型错误
5. `pytest -q` — 全套件通过（Phase C 后应全绿；test_api.py 重写后无失效端点测试）
6. 可选：SQLite 临时库 `Base.metadata.create_all` + 插入 `User`/`UserProfile`/`NutritionGoal`/`RecipeRecord` 验证模型层与 `recipes` 表名

### Phase E — 同步更新《CasaMind 项目分析报告.md》

新增章节（不改动既有 〇~八章历史基线），覆盖用户指定的四方面：
- **功能实现说明**：本次命名收敛覆盖的类/表/任务名/串/种子数据/测试清单
- **代码结构分析**：分层改动路径（models→schemas→ai→repos→services→api→worker→tests），与现有架构契合点
- **技术选型依据**：`RecipeRecord`/`TaskItem`/`MemberProfile` 命名为何与现有 `*Record`/`*Item` 模式一致；Celery 任务名重命名的影响域；表重命名在 `create_all` 迁移下的安全性
- **潜在优化点与风险**：见下

---

## 风险与注意事项

1. **表名重命名**（`household_recipes` → `recipes`）：`0001_initial_solochef.py` 是 `create_all`/`drop_all` 基，新表名下次建库生效；**旧 `household_recipes` 表数据不会自动迁移**（dev 项目可接受，生产前需 `RENAME TABLE` 或数据导出）。需在报告中明确提示。
2. **Celery 任务名重命名**（`casamind.*` → `solochef.*`）：若 Redis 中有旧路由的 in-flight 消息，会路由失败。dev 环境无在途消息可忽略；生产需清空队列或灰度。`celery_app = Celery("solochef")` 主名变更不影响已注册任务（按 `name=` 字符串路由）。
3. **种子文档重命名**会使用户既有 Chroma 集合中的旧文档名（"控糖家庭饮食原则.md"）与新种子不一致；需在报告中提示"重命名后应重新 `POST /knowledge/bootstrap` 灌入新标题文档，并清理旧文档"。
4. **test_api.py 重写工作量大**：约 15+ 个测试函数需映射改写。按函数逐个推进，每改一组即跑 `pytest` 验证，避免一次性大改难定位回归。`conftest.py` fixture 先行（注册流程去家庭化），否则后续测试全卡 setup。
5. **`FamilyMember` → `MemberProfile` 语义**：SoloChef 单用户下 `members` 列表实际退化为单元素或空，但本次**不改业务逻辑**（不删 `members` 字段、不改 workflow 注入逻辑），仅改名。后续如需真正移除成员概念，属另一独立任务。
6. **`feedback_loop.py` docstring `:Family`**：graph_store 实际已用 `:User`，docstring 是 stale 描述，本次修正对齐，不改 Cypher。
7. **保留项**：`graph_store.py`/`repositories/*.py`/`auth.py` 中"去家庭化版"说明性 docstring 保留（解释迁移历史，有审计价值）；alembic `0001_initial_solochef.py` 注释中 "Previous CasaMind family-oriented migrations were removed" 保留（历史说明）。

---

## 关键文件清单（代表性）

- 模型：`backend/app/models/identity.py`、`backend/app/models/__init__.py`
- Schema：`backend/app/schemas/domain.py`、`backend/app/schemas/__init__.py`
- AI：`backend/app/ai/workflow.py`、`backend/app/ai/prompts.py`、`backend/app/ai/domain_agents.py`、`backend/app/ai/llm.py`
- Repository：`backend/app/repositories/domain.py`、`backend/app/repositories/feedback.py`、`backend/app/repositories/planning.py`
- Service：`backend/app/services/domain.py`、`backend/app/services/nutrition.py`、`backend/app/services/planning.py`、`backend/app/services/calendar_planning.py`、`backend/app/services/demo_data.py`、`backend/app/services/feedback_loop.py`、`backend/app/services/query_rewriter.py`、`backend/app/services/entity_extractor.py`、`backend/app/services/knowledge.py`、`backend/app/services/rag_eval.py`
- API：`backend/app/api/router.py`
- Worker：`backend/app/worker.py`
- DB：`backend/app/db/base.py`
- 测试：`backend/tests/conftest.py`、`backend/tests/test_rag.py`、`backend/tests/test_graph_rag_quality.py`、`backend/tests/test_api.py`
- 报告：`CasaMind 项目分析报告.md`

## 验证方式（端到端）

1. 后端导入：`cd backend && python -c "from app.main import app; print(len(app.routes))"`
2. 静态检查：`ruff check app tests alembic` && `mypy app tests`
3. 单元/集成测试：`python -m pytest -q`（Phase C 完成后应全绿）
4. 模型建表冒烟（SQLite）：临时 `DATABASE_URL=sqlite+aiosqlite:///./solochef.db` 启动，确认 `recipes` 表创建、`RecipeRecord` CRUD 正常
5. 报告审阅：人工核对《CasaMind 项目分析报告.md》新增章节的四方面完整性

# CasaMind 项目分析报告

> ⚠️ **本文档为历史版本**。最新专业化报告已迁移至 [SoloChef 项目分析报告.md](file:///d:/pyton_feisi/project/project_agent/SoloChef%20项目分析报告.md)（含业务流程图、功能实现状态、进度评估、风险分析、工作计划）。本文件保留为去家庭化迁移过程的历史记录与工程细节参考。

> 更新日期：2026-08-07（SoloChef 收敛 + 去家庭化迁移主体完成 + MySQL 适配落地 + 代码质量全绿 + 战略分析）。本文档顶部「〇」章节为**当前最新状态**；第二至八章保留为定位收敛前 CasaMind 家庭导向状态的历史基线与分析，供迁移对照。SoloChef 目标状态以 `HomePilot_PRD.md` 为准。本轮新增：后端去家庭化运行时收尾、MySQL dialect 适配、独立 SoloChef 前端原型、SQLite 端到端验证、**代码质量验证（ruff/mypy/pytest 全绿）与工程化分析（〇.8 节）**、**战略分析与商业规划（〇.9 节：价值主张/用户画像/竞品分析/架构适配/商业模式/路线图）**。

## 〇、SoloChef 收敛与迁移进展（2026-08-07：后端去家庭化 + MySQL 完成）

经项目定位评估，原「CasaMind / HomePilot 家庭综合事务规划师」方向过宽（覆盖日历/餐食/采购/任务/预算/知识库/对话/协作/库存九大域，23 张表因范围蔓延而冗余），已收敛为 **SoloChef — AI 独居膳食与采买规划师**。定位、PRD、代码层去家庭化迁移与 MySQL 适配已落地，进展如下。

### 〇.1 收敛要点

- 定位：独居自炊 + 营养目标驱动（增肌 / 减脂 / 健康维护 + 性别差异），核心闭环 = 营养目标 → 每日三餐 → 购物清单 → 食材替换联动 → 执行反馈学习。
- 架构决策：**去家庭化（不保留 family 抽象）**——删除 `families/family_memberships/family_member_profiles/family_invitations/event_participants`，所有业务表 `family_id → user_id`，画像迁入 `user_profiles`，新增 `nutrition_goals`（TDEE + 宏量分配），见 PRD 5.5。
- 数据库：主库由 PostgreSQL/SQLite 改为 **MySQL**（求职认知度优先；已处理 dialect 适配，无 `postgresql_where`/数组/Postgres-only `server_default`），Neo4j + Chroma 保留。
- 表结构：由 23 张家庭导向表收敛为 20 张以 `user_id` 为核心的聚焦表（删 5 家庭表、增 `user_profiles`/`nutrition_goals`）。
- 砍除：家庭协同、任务看板、完整日历、通用知识库问答、库存、通知、外部同步、商超价格比对、剩余食材利用。
- 降级可选：女性生理周期（非医疗化）、预算（采购成本面轻量保留）。

### 〇.2 后端去家庭化迁移（Phase 1–2，完成）

- **模型层（Phase 1）**：重写 `backend/app/models/identity.py`——20 张表，删 5 张家庭表，新增 `UserProfile`（身体数据 + 饮食偏好/忌口/预算）与 `NutritionGoal`（TDEE + 宏量分配），所有业务表 `family_id → user_id`，`WeeklyPlan` 部分唯一索引改普通索引（MySQL 兼容），`PlanTask.assignee_member_id`/`TaskCompletion.member_profile_id`/`PlanFeedback.member_profile_id` 列移除。`models/__init__.py` 同步更新。
- **认证核心（Phase 2）**：`core/security.py` TokenClaims 去 `family_id`/`role`；`schemas/auth.py` 删 `FamilyRole`/`FamilySummary`/`Invitation*`；`repositories/identity.py` 改 user-only（删家庭/成员/邀请 CRUD）；`services/auth.py` 注册不再建家庭；`api/dependencies.py` AuthContext 去 `family_id`/`family_name`/`role`（`require_roles` 降级为恒放行）；`schemas/__init__.py` 移除家庭 auth 导出。
- **Repository 层**：`calendar.py`（去 EventParticipant/FamilyMemberProfile，冲突改纯时间重叠）、`domain.py`（单用户，`complete_task` 去 `member_profile_id`，工作量改按 `completed_by_user_id`）、`planning.py`（整体 user 维度）、`conversations.py`、`feedback.py` 全部 `family_id → user_id`。
- **Services 层**：`nutrition.py`（改用 targets dict）、`domain.py`（单用户）、`feedback_loop.py`（FeedbackSignal 去 `family_id`/`member_profile_id`/`member_name`）、`graph_store.py`（`:Family → :User`、`sync_family_context → sync_user_context`、Cypher 全量改 user 维度）、`knowledge.py`/`vector_store.py`/`conversation.py`/`worker.py`/`workflow.py`/`evaluation.py`/`rag_eval.py`/`domain_agents.py`/`calendar_planning.py` 全部去家庭化。
- **API 层**：`auth_router.py` 重写为 12 路由（删家庭/成员/邀请端点，保留认证 + 短信 + 设备会话）；`router.py` 93 路由（删成员 CRUD 与 `_member_response`/`_family_members`），`context.family_id → context.user_id`。
- **里程碑**：`from app.main import app` 导入成功，93 主路由 + 12 认证路由，0 成员/家庭端点。

### 〇.3 后端运行时收尾（2026-08-07：修复"导入通过但调用即崩"的残留）

去家庭化的机械替换虽使应用可导入，但若干端点在运行时仍引用已删方法/字段，本轮逐一修复：

- `services/conversation.py`：`_planning_request` 重复 `user_id` 参数（签名 + `PlanningRequest` 调用）→ 修复，此前为 SyntaxError 无法导入。
- `schemas/domain.py`：`Dashboard` 去 `family_name`/`members`、改 `user_name`；`KnowledgeTextRequest`/`KnowledgeSearchRequest` 的 `family_id → user_id`，category 默认"家庭知识"→"个人知识"。
- `api/router.py`：
  - dashboard 端点两处 `context.family_name → context.display_name`，移除 `members=[]`。
  - `_task_write_values`：删已删的 `IdentityRepository.get_member_profile` 调用，独居场景 `assignee` 直接取 `context.display_name`。
  - `update_calendar_event`：`record.participants`（已删属性）→ `participant_ids=[]`。
  - `create_meal`：删重复 `context.user_id` 位置参数（`create_meal(user_id, **values)` 只需一个）。
  - `meal_nutrition`：原 `build_nutrition_report(meals, recipes, members)` 误传 list 给 targets——改为从 `NutritionGoal` 取 TDEE/宏量构建 targets dict（营养目标驱动）。
  - `complete_task` 反馈信号：删 `member_profile_id`/`member_name`（`PlanFeedback` 已无这两列）。
  - 移除 `FamilyMember`/`FamilyMemberCreate`/`FamilyMemberUpdate` 导入，新增 `select` + `NutritionGoal` 导入。
- `services/demo_data.py`：`get_dashboard()` 改用 `user_name`、去 `members`；删 `MEMBERS` 与对应导入。

### 〇.4 MySQL 适配（Phase 3，完成）

- `core/config.py`：`database_url` 默认改为 `mysql+aiomysql://solochef:solochef_password@localhost:3306/solochef`；`app_name → SoloChef API`；`jwt_issuer → solochef-api`；`chroma_collection → solochef_knowledge`。
- `docker-compose.yml`：`postgres:16` 整体替换为 `mysql:8.0`（utf8mb4 字符集 + `mysqladmin ping` 健康检查），backend/worker 的 `DATABASE_URL` 改 `mysql+aiomysql://...@mysql:3306/solochef`，depends_on 与 volumes（`mysql_data`）同步；neo4j/chroma 密码前缀改 `solochef`。
- `alembic/env.py`：删 `Family`/`FamilyMemberProfile`/`FamilyMembership` 导入（已删模型，否则 `alembic upgrade` 崩），改 `import app.models` 触发全表注册。
- 删除 12 个陈旧 postgres 迁移（引用已删家庭表），新增 `alembic/versions/0001_initial_solochef.py`：**dialect-agnostic** `Base.metadata.create_all/drop_all`，兼容 SQLite/MySQL/Postgres。
- `app/main.py`：lifespan 启动调用 `_create_tables()`（幂等 `create_all`，异常静默），开箱即用建表。
- `pyproject.toml`/`requirements.txt`：`asyncpg → aiomysql>=0.2.0`；Dockerfile 仍 `pip install ".[ai]"`（已含 aiomysql），CMD `alembic upgrade head && uvicorn ...` 仍可用。
- **dialect 兼容确认**：`planning.py`/`checkpoints.py` 的 postgres-only 分支（PostgresSaver/checkpointer）对 MySQL 优雅降级（不挂 checkpoint，不报错）；模型无 `postgresql_where`/数组/Postgres-only `server_default(JSON)`，JSON 列用 `default=list` 兼容 MySQL。

### 〇.5 前端进展

- 新增 `solochef-prototype.html`：独立、零依赖（纯 CSS/SVG，无外链/占位图）的 SoloChef 产品原型，含非对称 Hero、三步闭环、可切换 App 演示（仪表盘 / **真实 Mifflin-St Jeor 营养计算器（性别 ±161、减脂 −15%/增肌 +10%、蛋白 1.8–2.0 g/kg）** / 本周三餐 / 采购清单）、技术栈展示。
- `frontend/`（`casamind-web`，Vue 3.5 + Vite 7 + Pinia 3 + axios + ECharts 6 + sass）**仍为家庭模型**，`api.ts`/`types.ts`/`views`/`stores` 需后续并行去家庭化并接入营养目标表单与每日三餐/采购 UI（可参考原型交互与文案）。

### 〇.6 验证结果（2026-08-07）

- `from app.main import app` 导入通过（93 + 12 路由）。
- `demo_data.get_dashboard()` 正常返回 `user_name=小王`。
- **端到端验证**：以 SQLite 临时库 `Base.metadata.create_all` + 插入 `User`/`UserProfile`/`NutritionGoal` + commit 全部成功，确认去家庭化模型层无遗漏、关系 `back_populates`（`User.profile`/`User.nutrition_goal`）正常。
- **代码质量全绿（Phase D，2026-08-07）**：
  - `ruff check app tests`：**All checks passed!**（修复 17 项：14 项 import 排序/未使用导入自动修复 + 3 项手动修复——`evaluation.py` 的 `workload` 未定义变量 bug、`calendar_planning.py`/`graph_store.py` 行过长）。
  - `mypy app`：**Success: no issues found in 54 source files**（修复 32 项：21 项可选 AI 依赖缺失导入配置忽略 + `retrieve_vector` 返回值 3-tuple 解包 + `KnowledgeRetriever` Protocol 返回类型同步 + `members` 参数类型统一为 `Sequence[MemberProfile]` + `router.py` 8 处空列表类型注解）。
  - `pytest -q`：**51 passed, 3 warnings**（修复 `StubKnowledgeRetriever.retrieve_vector` 返回 3 值适配 + `test_rag.py` 两处 stub 返回类型同步）。
- 说明：本地 venv 由 uv 管理、未装 `aiomysql`，但 dialect 惰性加载使导入仍通过；真实运行走 `docker-compose up`（镜像含 aiomysql）。本地零配置开发可设 `DATABASE_URL=sqlite+aiosqlite:///./solochef.db`。

### 〇.7 当前状态结论

| 维度 | 状态 |
|---|---|
| 定位收敛 + PRD | ✅ 完成（SoloChef，`HomePilot_PRD.md` 已重写含 5.5 去家庭化小节） |
| 后端去家庭化（模型/认证/repo/service/router） | ✅ 完成（导入通过，运行时残留已修） |
| MySQL 适配（config/compose/migration/dialect） | ✅ 完成（SQLite 端到端验证通过；MySQL 真机联调待 docker 起库） |
| 前端 | ⏳ 仅有独立原型 `solochef-prototype.html`；Vue 应用去家庭化待做 |
| 营养新功能（Phase 5） | ⏳ 计算器原型已就位；后端 `NutritionGoal` 写入端点 + 规划工作流接营养约束待做 |
| 代码质量（ruff/mypy/pytest） | ✅ 全绿（Phase D：ruff 0 项、mypy 54 文件 0 错误、pytest 51 passed） |
| 战略分析与商业规划 | ✅ 完成（〇.9 节：价值主张/用户画像/竞品分析/架构适配/商业模式/路线图） |
| **下一步最高优先级** | **Phase 1：前端核心闭环**（详见 〇.9.6 路线图，80% 工程资源应向前端倾斜） |

### 〇.8 代码质量验证与工程化分析（Phase D–E，2026-08-07）

本节为去家庭化命名收尾后的代码质量验证与工程化分析，涵盖功能实现说明、技术选型依据、代码结构分析、潜在优化点及风险评估。

#### 〇.8.1 功能实现说明

去家庭化命名收尾后，后端代码已完成以下修复与验证：

| 修复项 | 文件 | 说明 |
|---|---|---|
| `workload` 未定义变量 | `app/ai/evaluation.py:143` | 去家庭化后 `_score_task` 仍引用已删除的 `workload` 字典，SoloChef 单用户场景下公平度恒 100，移除该死引用 |
| `retrieve_vector` 返回值解包 | `app/ai/workflow.py:251` | `KnowledgeService.retrieve_vector` 返回 3-tuple（hits, chroma_status, rerank_status），workflow 仅解包 2 值，补齐为 `hits, status, _rerank_status` |
| `KnowledgeRetriever` Protocol 返回类型 | `app/ai/workflow.py:73-75` | Protocol 声明 `retrieve_vector` 返回 2-tuple，与实际实现 3-tuple 不一致，同步为 `tuple[list[VectorSearchHit], str, str]` |
| `members` 参数类型统一 | `knowledge.py`/`conversation.py`/`router.py` | `PlanningService.generate` 期望 `Sequence[MemberProfile]`，`KnowledgeService` 方法期望 `Sequence[dict[str, object]]`，`ConversationService` 传递 `Sequence[dict[str, object]]`——全链路统一为 `Sequence[MemberProfile]` |
| `router.py` 空列表类型注解 | 8 处 `profiles = []`/`members = []` | mypy 无法推断空列表类型，添加 `list[MemberProfile]` 显式注解 |
| Import 排序与未使用导入 | 14 处（`evaluation.py`/`workflow.py`/`router.py`/`models/__init__.py` 等） | ruff `--fix` 自动修复 I001（import 排序）与 F401（未使用导入：`defaultdict`/`IdentityRepository`） |
| 行过长 | `calendar_planning.py:33`/`graph_store.py:205` | 函数签名拆行 / docstring 换行，适配 `line-length = 100` |
| 测试 stub 返回值同步 | `conftest.py:24`/`test_rag.py:54,217` | `StubKnowledgeRetriever.retrieve_vector` 及 test_rag.py 两处 stub 返回 2 值 → 3 值，适配 workflow 解包 |
| 可选依赖缺失导入 | `pyproject.toml [tool.mypy.overrides]` | langchain_core/langgraph/chromadb/neo4j 等 9 个可选 AI/ML 包在开发环境未安装时 `ignore_missing_imports = true` |

#### 〇.8.2 技术选型依据

| 技术决策 | 选型 | 依据 |
|---|---|---|
| 依赖管理 | uv 0.12 | 项目已有 `uv.lock`，`uv sync --all-extras` 一键安装全部依赖（含 ai/bge/rerank/dev 四个 extra），比 pip 快 10x+ |
| 代码风格 | ruff 0.16（E/F/I/UP/B/SIM 规则集） | 替代 black + isort + flake8 三件套，单工具覆盖 import 排序、未使用导入、现代 Python 语法升级、bugbear、简化建议 |
| 类型检查 | mypy 1.20（strict: `disallow_untyped_defs`） | 配合 `pydantic.mypy` 插件，54 个源文件 0 错误；可选依赖通过 `[[tool.mypy.overrides]]` 精细控制 |
| LangChain 生态升级 | langchain 1.3 + langgraph 1.2 | langgraph 0.5 在 Python 3.12 上存在 MRO（Method Resolution Order）冲突（`PregelProtocol(Runnable, Generic, ABC)` 触发 `TypeError: Cannot create a consistent MRO`），升级到 1.x 解决；langchain 同步升级到 1.x 保持依赖一致性 |
| 测试数据库 | SQLite（`sqlite+aiosqlite:///:memory:`） | conftest.py 已用内存 SQLite 隔离测试，无需外部数据库；生产环境走 MySQL（`mysql+aiomysql://`） |

#### 〇.8.3 代码结构分析

```
backend/app/
├── ai/                    # AI 规划核心
│   ├── workflow.py        # 13 节点 LangGraph StateGraph（含 KnowledgeRetriever Protocol）
│   ├── domain_agents.py   # 四领域智能体（meal/shopping/task/budget）
│   ├── evaluation.py      # 多维度加权评分（餐35/购25/任25/预15）
│   ├── llm.py             # LLM 抽象（Demo/OpenAI 兼容）
│   └── prompts.py         # Prompt 版本注册表
├── api/
│   ├── router.py          # 93 路由（dashboard/calendar/tasks/meals/shopping/...）
│   ├── auth_router.py     # 12 认证路由（register/login/sms/sessions/...）
│   └── dependencies.py    # CurrentContext/OwnerContext/SessionDep
├── models/identity.py     # 20 张去家庭化表（user_id 核心）
├── repositories/          # Repository 模式（user_id 隔离 + selectinload）
├── schemas/               # Pydantic v2 schema（domain.py 集中定义）
├── services/              # 21 个服务模块（planning/knowledge/graph_store/...）
└── worker.py              # Celery 4 队列 + DeadLetterTask
```

**分层架构**：Router → Service → Repository → Model（SQLAlchemy 2.x `Mapped`/`mapped_column` + `TimestampMixin`），Pydantic v2 schema 做请求/响应隔离。所有 Repository 方法带 `user_id` 隔离参数，符合多租户安全模型（虽 SoloChef 已收敛为单用户，但 `user_id` 隔离保留为安全边界）。

**类型一致性**：`members` 参数在 `PlanningService.generate()` / `KnowledgeService.retrieve_graph()` / `ConversationService.run_turn()` / `SoloChefWorkflow` 全链路统一为 `Sequence[MemberProfile]`，`KnowledgeRetriever` Protocol 与实现签名对齐，消除去家庭化重构遗留的类型不匹配。

#### 〇.8.4 潜在优化点

1. **`app/db/session.py` 引擎创建时机**：`engine = create_async_engine(settings.database_url)` 在模块导入时执行，导致测试环境必须安装生产数据库驱动（asyncpg/aiomysql）才能 import。建议改为延迟初始化（`@lru_cache` 工厂函数）或测试环境注入 `DATABASE_URL=sqlite+aiosqlite:///:memory:` 环境变量。
2. **`.env` 数据库 URL 与 pyproject.toml 依赖不一致**：`.env` 配置 `postgresql+asyncpg://` 但 `pyproject.toml` 依赖列表为 `psycopg[binary,pool]`（无 asyncpg）。建议统一为 `postgresql+psycopg_async://` 或在 `.env` 中改用 MySQL URL 匹配 config.py 默认值。
3. **`KnowledgeService` 中 `members` 参数为死代码**：`retrieve_graph`/`retrieve`/`search`/`bootstrap` 均接收 `members` 参数但未使用（`sync_user_context` 第二参数传 `None`）。可在后续清理中移除该参数简化接口。
4. **`evaluation.py` `_score_task` 公平度恒 100**：SoloChef 单用户场景下 `fairness = 100.0` 硬编码，`workload` 字典已移除。如后续恢复多用户或引入"任务负载均衡"维度，需重新实现公平度算法。
5. **langgraph 版本约束**：`pyproject.toml` 已从 `>=0.2,<1.0` 升级到 `>=1.0,<3.0`，`uv.lock` 已重新生成。但 langchain 1.x 可能引入 breaking changes，需关注 `langchain_openai.ChatOpenAI` 等核心 API 的兼容性。

#### 〇.8.5 风险评估

| 风险项 | 等级 | 说明 | 缓解措施 |
|---|---|---|---|
| langchain 1.x 升级 breaking changes | 中 | langchain 0.3 → 1.3 跨大版本，`ChatOpenAI.astream()`/`RunnableConfig` 等 API 可能有行为变化 | 51 项测试全通过覆盖核心链路；持续观察 LLM 真机调用行为 |
| `.env` asyncpg 缺失致导入失败 | 中 | 生产部署如沿用 `.env` 的 `postgresql+asyncpg://` 但 Dockerfile 未装 asyncpg，导入即崩 | Dockerfile `pip install ".[ai]"` 不含 asyncpg；需改 `.env` 为 MySQL URL 或在 dependencies 增加 asyncpg |
| 可选 AI 依赖缺失降级链路未测试 | 低 | mypy `ignore_missing_imports` 跳过类型检查，但运行时降级路径（reranker/entity_extractor/query_rewriter 返回 None/回退）仅有单元测试覆盖 | 真机部署时确保 `pip install ".[ai,bge,rerank]"` 安装全量可选依赖 |
| `members` 死参数接口膨胀 | 低 | 4 个 KnowledgeService 方法保留未使用的 `members` 参数，增加接口理解成本 | 后续清理迭代中移除 |
| SQLite 测试与 MySQL 生产差异 | 低 | 测试用 SQLite（无 `postgresql_where`/数组/JSON 方言差异），生产用 MySQL，部分 SQL 行为可能不一致 | Alembic `0001_initial_solochef.py` 用 `Base.metadata.create_all` dialect-agnostic；MySQL 真机联调待 docker 起库 |

> **迁移状态**：代码层去家庭化 + MySQL 适配**已完成**。本报告第二至八章描述的是**定位收敛前 CasaMind 家庭导向状态**（PostgreSQL 默认、23 张家庭表、家庭协同功能），保留为历史基线与迁移对照；其"当前状态"口径以本「〇」章节为准，不再代表运行时代码。

### 〇.9 战略分析与商业规划（2026-08-07）

本节基于 SoloChef 当前定位（`HomePilot_PRD.md`）、代码实际状态（〇.1–〇.8）和市场环境，对项目进行全面的战略层面分析，指导后续资源配置与方向决策。

#### 〇.9.1 项目核心目标与价值主张

**一句话定位**：SoloChef 是面向独居自炊用户的 AI 个人目标营养备餐助手，围绕"身体数据 → 营养目标 → 三餐计划 → 购物清单 → 执行反馈"高频闭环构建。

**价值主张层级**：

| 层级 | 内容 | 用户感知 |
|---|---|---|
| 功能价值 | 基于增肌/减脂/健康目标，自动生成营养达标的三餐 + 精确购物清单 | "不用每天想吃什么、买什么" |
| 效率价值 | AI 多智能体协同规划 + 食材替换联动 + 预算校验，减少决策和返工 | "3 分钟出完整一周计划" |
| 沉淀价值 | 反馈回流 Graph RAG + 向量记忆，越用越懂个人口味和预算 | "AI 记住了我不吃香菜、常买的那家鸡胸肉涨价了" |
| 差异价值 | 不是"AI 生成菜谱"玩具，而是目标驱动的营养约束系统 | "终于知道每顿饭是否达标、差在哪" |

**核心洞察**：SoloChef 的差异点不在于"LLM 生成菜谱"（门槛很低），而在于**约束计算 → 计划校验 → 购物联动 → 反馈学习**的完整闭环。PRD 第 62 行明确指出："必须避免'输入一句话 → LLM 线性生成 → 结束'的玩具化问题"。当前代码的 13 节点 LangGraph 工作流（Graph Retriever → Vector Retriever → 4 领域 Agent → Verifier）正是这一差异的技术实现。

#### 〇.9.2 目标用户群体特征与需求分析

**三类核心用户画像**：

| 画像 | 增肌健身者 | 减脂塑形者 | 健康维护者 |
|---|---|---|---|
| **年龄/性别** | 22-35 岁，男性为主 | 25-40 岁，女性偏多 | 28-45 岁，性别均衡 |
| **烹饪频率** | 每周 5-7 次自炊（备餐文化） | 每周 3-5 次自炊 | 每周 3-4 次自炊 |
| **核心痛点** | 蛋白质摄入不足、食材重复、备餐耗时 | 热量控制难、外卖诱惑、饱腹感不足 | 三餐不规律、食材单一、决策疲劳 |
| **营养知识** | 较专业（知道 BMR/TDEE/宏量营养） | 中等（关注卡路里但对宏量营养模糊） | 较低（凭感觉吃） |
| **付费意愿** | 高（已习惯为健身 App/补剂付费） | 中高（为身材管理付费意愿强） | 中低（为便利付费，但价格敏感） |
| **使用频率** | 每日（备餐日高频查看） | 每日（三餐打卡） | 每周 2-3 次（生成计划时） |
| **留存驱动** | 营养达标率、蛋白摄入趋势 | 体重/体脂趋势、热量赤字 | 减少决策成本、食材多样性 |

**需求优先级矩阵**：

```text
高价值 × 高频次（必做 P0）：
  ├─ 营养目标计算（BMR/TDEE/宏量营养）── 所有用户的核心入口
  ├─ 三餐 AI 生成 + 营养达标校验 ── 每日高频使用
  ├─ 购物清单自动生成 + 预算估算 ── 采购日刚需
  └─ 食材替换 + 营养联动重算 ── 真实场景必然发生

高价值 × 低频次（增强 P1）：
  ├─ 每周营养复盘报告 ── 周度回顾
  ├─ Agent 轨迹可视化（推理过程透明）── 建立信任
  └─ 菜谱知识库管理 ── 偶尔上传/管理

低价值/不做（P2/排除）：
  ├─ 女性周期饮食标记（P2，自愿开启）
  ├─ 家庭协作/成员管理（明确不做）
  ├─ 通用日历/家务任务（明确不做）
  └─ 实时商超比价（明确不做，用历史均价替代）
```

**关键使用场景**：
1. **周日备餐日**：用户生成一周三餐计划 → 导出购物清单 → 周日集中采购 → 批量备餐
2. **工作日早餐**：快速查看今日三餐 → 确认食材已备 → 记录实际吃了什么
3. **临时换菜**：下班后发现某食材没了 → 替换食材 → AI 重算营养和购物清单
4. **周末复盘**：查看本周营养达标率、预算偏差、口味反馈 → 调整下周目标

#### 〇.9.3 市场竞争格局与差异化优势

**竞品分析**：

| 竞品 | 定位 | 优势 | 不足 | SoloChef 差异点 |
|---|---|---|---|---|
| 薄荷健康 | 卡路里记录 + 食物库 | 庞大食物数据库、用户基数 | 仅记录不规划、无 AI 生成、无购物清单 | SoloChef 主动生成而非被动记录 |
| Keep | 健身 + 饮食 | 健身内容生态、品牌认知 | 饮食功能弱、菜谱泛化、无营养约束计算 | SoloChef 营养目标驱动的备餐闭环 |
| 下厨房 | 菜谱社区 | 海量 UGC 菜谱、社交属性 | 无营养目标、无计划生成、无购物清单 | SoloChef 从"找菜谱"升级到"被规划" |
| Yazio/MyFitnessPal | 卡路里追踪 | 国际化、营养数据库全 | 不懂中餐、无 AI 规划、无采购联动 | SoloChef 中餐优先 + AI 规划 + 购物闭环 |
| ChatGPT/豆包 通用 AI | 通用对话 | 能力全面、用户量大 | 无营养计算、无知识图谱、无反馈沉淀、无购物清单 | SoloChef 垂直闭环 + Graph RAG 记忆 |

**SoloChef 差异化优势（护城河）**：

1. **营养约束计算引擎**：Mifflin-St Jeor 公式计算 BMR/TDEE → 宏量营养目标 → AI 规划读取为硬约束 → Verifier 校验达标。通用 AI 做"生成菜谱"容易，做"在 2200 kcal / 140g 蛋白约束下生成三餐并校验达标"很难。
2. **Graph RAG 反馈学习闭环**：Neo4j 存储用户-食材-菜谱-偏好关系图谱，Chroma 存储语义知识向量。每次反馈（喜欢/不喜欢/替换原因/实际花费）回流到图谱和向量记忆，下次规划时检索引用。这是"越用越懂你"的技术基础，通用 AI 工具无法实现。
3. **三餐 → 购物 → 反馈全链路联动**：食谱替换 → 购物清单自动更新 → 营养重算 → 预算校验。市面工具要么只做菜谱、要么只做记录，缺乏跨环节联动。
4. **多智能体协同规划**：LangGraph 13 节点工作流（Graph Retriever → Vector Retriever → Meal/Shopping/Task/Budget Agent → Verifier），比单轮 LLM 生成更可控、可调试、可恢复。

**竞争风险**：
- 通用 AI（GPT/Claude）能力持续提升，"生成菜谱"门槛将进一步降低
- 大厂（字节/阿里）可能在主 App 中集成类似功能，挤压独立产品空间
- **应对策略**：深耕垂直闭环（营养约束 + Graph RAG 反馈 + 购物联动），建立数据护城河（用户反馈积累越多越难迁移）

#### 〇.9.4 产品功能模块与技术架构适配性分析

**功能-技术映射**：

| 功能模块（PRD） | 技术实现 | 适配性评估 | 状态 |
|---|---|---|---|
| F1 用户画像与营养目标 | `NutritionGoal` 模型 + `nutrition.py` 计算引擎 | ✅ 适配 | 后端完成，前端待接 |
| F2 AI 三餐计划 | LangGraph 13 节点 + 4 领域 Agent | ✅ 适配 | 核心闭环完成 |
| F3 购物清单与预算 | `ShoppingAgent` + `domain.py` 合并/分类/估算 | ✅ 适配 | 后端完成 |
| F4 食材替换与营养联动 | `MealReplacementRequest` + 反馈回路 | ✅ 适配 | 后端完成 |
| F5 Graph RAG 饮食知识 | Neo4j + Chroma + reranker + 实体抽取 | ✅ 适配 | 后端完成（2.20 节） |
| F6 Agent 轨迹 | `AgentStep` SSE 流 + `AgentRunRecord` 持久化 | ⚠️ 过度建设 | P1，建议降级为内嵌（见上文建议） |
| F7 执行反馈与复盘 | `FeedbackRepository` + `feedback_loop_service` | ✅ 适配 | 后端完成 |
| F8 女性周期标记 | 无独立实现 | ⏳ P2 | 明确推迟 |

**架构适配性诊断**：

| 维度 | 评估 | 说明 |
|---|---|---|
| **过度建设** | 日历模块（`CalendarEventRecord` + RRULE 展开 + 例外管理）、任务模块（`PlanTask` + 自动展开 + recurring）、库存模块（`InventoryItem`） | 这些是 CasaMind 家庭综合规划遗留，与 SoloChef"营养备餐"定位无关。PRD 第 26-30 行已明确不做家务/日历/库存。建议降级为 P3 或移除 |
| **适度建设** | Celery 4 队列隔离、死信处理、幂等键 API、SSE 断线重连 | 工程化质量高，但部分能力（如 4 队列隔离）在单用户场景下可简化为 2 队列（AI 规划 + 后台同步） |
| **建设不足** | 营养目标前端交互、三餐可视化、购物清单移动端体验、反馈采集 UI | 后端能力强但前端尚未对接，导致核心闭环"后端跑通但用户不可见" |
| **技术债务** | `.env` 数据库 URL 与依赖不一致（asyncpg vs psycopg）、`members` 死参数、`evaluation.py` 公平度硬编码 | 见 〇.8.4 潜在优化点 |

**架构调整建议**：
1. **收敛功能边界**：将日历/任务/库存模块从主路由移除（保留代码但不在导航暴露），聚焦"营养 → 三餐 → 购物 → 反馈"核心闭环
2. **前端优先**：当前最大瓶颈是前端未对接后端能力，应将工程资源向前端倾斜
3. **简化队列**：4 队列 → 2 队列（`ai_planning` + `background_sync`），降低运维复杂度

#### 〇.9.5 商业模式与盈利路径

**定位**：个人项目 / 求职作品集 → 验证产品价值 → 探索商业化

**当前阶段（Phase 0-1）**：不商业化，聚焦产品验证和技术展示

| 阶段 | 时间 | 目标 | 商业化策略 |
|---|---|---|---|
| Phase 0（当前） | 已完成 | 后端核心闭环 + 代码质量全绿 | 无收入，作品集/面试展示 |
| Phase 1 | 1-2 个月 | 前端核心闭环 + 真机部署可演示 | 无收入，小范围用户测试（10-20 人） |
| Phase 2 | 3-6 个月 | 种子用户验证留存 | 免费试用 + 邀码制，收集留存和 NPS 数据 |
| Phase 3 | 6-12 个月 | 若留存验证通过，探索付费 | Freemium 模式（见下） |

**Freemium 模式设计（Phase 3 探索）**：

| 层级 | 价格 | 功能 | 目标用户 |
|---|---|---|---|
| 免费版 | ¥0 | 每周 3 次 AI 规划、基础营养目标、7 天历史 | 新用户体验、降低门槛 |
| Pro 版 | ¥19/月 或 ¥168/年 | 无限规划、Graph RAG 个性化学习、每周复盘报告、菜谱知识库上传 | 增肌/减脂高频用户 |
| Pro+ 版 | ¥39/月 | Pro 全部 + 训练日/休息日策略、食材价格趋势、优先 LLM 推理 | 深度健身用户 |

**成本结构**：

| 成本项 | 月估（100 活跃用户） | 说明 |
|---|---|---|
| LLM API | ¥200-500 | GPT-4o-mini 或国产模型（deepseek/qwen），单次规划约 ¥0.05-0.15 |
| 服务器 | ¥100-200 | 2C4G 云主机（MySQL + Redis + FastAPI） |
| Neo4j + Chroma | ¥100 | 可与主服务同机部署或用云服务 |
| 短信验证 | ¥50 | 阿里云号码认证，¥0.045/条 |
| 域名/CDN | ¥20 | 基础设施 |
| **合计** | **¥470-870/月** | 100 用户时 Pro 转化率 10%（10 人 × ¥19）= ¥190，不足以覆盖成本 |

**盈利路径结论**：个人项目短期内不以盈利为目标。若 Phase 2 留存验证通过（次周留存 > 40%），可在 Phase 3 探索 Freemium。LLM 成本是最大变量，建议支持多模型切换（OpenAI / DeepSeek / Qwen）以优化成本。当前阶段的核心价值是**技术能力展示和产品思维验证**。

#### 〇.9.6 实施路线图与关键里程碑

**当前状态**：后端核心闭环完成（营养计算 → AI 规划 → 购物清单 → 食材替换 → 反馈回流），代码质量全绿（ruff/mypy/pytest 51 通过），但前端尚未对接后端能力。

**调整后路线图**：

```text
Phase 1：前端核心闭环（1-2 个月）── 最高优先级
├─ 1.1 前端去家庭化：router/AppShell/页面组件移除 CasaMind/家庭/日历/任务旧入口
├─ 1.2 营养目标页：身体数据输入 → BMR/TDEE 计算 → 宏量营养目标展示
├─ 1.3 AI 备餐规划页：自然语言输入 → SSE 流式展示规划过程 → 三餐 + 购物清单结果
├─ 1.4 三餐计划页：日历视图 + 单餐替换 + 营养达标进度条
├─ 1.5 购物清单页：分类列表 + 勾选购买 + 实际花费核销
└─ 1.6 反馈复盘页：餐后评分 + 周度营养达标率 + 预算偏差

里程碑 M1：前端核心闭环可演示（端到端走通"注册 → 设目标 → 生成计划 → 采购 → 反馈"）

Phase 2：真机部署 + 用户验证（2-3 个月）
├─ 2.1 Docker Compose 真机部署（MySQL + Redis + Neo4j + Chroma + FastAPI + Celery）
├─ 2.2 LLM 真机接入（OpenAI / DeepSeek 多模型切换）
├─ 2.3 短信验证真机（阿里云号码认证，已修复 SDK）
├─ 2.4 小范围用户测试（10-20 人，收集留存和反馈）
└─ 2.5 功能收敛：日历/任务/库存模块从导航移除或降级

里程碑 M2：10+ 真实用户使用，次周留存 > 30%

Phase 3：产品深化 + 商业化探索（3-6 个月）
├─ 3.1 Graph RAG 反馈学习效果验证（用户反馈是否提升推荐质量）
├─ 3.2 每周营养复盘报告 + ECharts 可视化
├─ 3.3 PWA 化 + 移动端体验优化
├─ 3.4 若 M2 留存达标，探索 Freemium 付费模式
└─ 3.5 菜谱知识库社区化（用户上传菜谱 → 向量化 → 共享）

里程碑 M3：付费用户 > 10 人 或 作品集完整度可支撑求职面试

Phase 4：长期演进（6+ 个月，条件触发）
├─ 4.1 训练日/休息日营养策略（P2）
├─ 4.2 食材价格数据导入（P2，不做实时比价）
├─ 4.3 多端适配（小程序 / App）
└─ 4.4 开放 API / 集成第三方健康设备
```

**关键里程碑与验收标准**：

| 里程碑 | 验收标准 | 当前进度 |
|---|---|---|
| M0：后端核心闭环 | 51 测试全绿 + ruff/mypy 零错误 + 营养/规划/购物/反馈 API 可调用 | ✅ 已完成 |
| M1：前端核心闭环 | 浏览器端走通"注册→设目标→生成计划→采购→反馈"全流程 | ⏳ 待启动（最高优先级） |
| M2：真机用户验证 | 10+ 真实用户、次周留存 > 30%、收集 50+ 条有效反馈 | ⏳ M1 完成后启动 |
| M3：商业化/求职就绪 | 付费用户 > 10 人 或 面试可完整演示技术深度 + 产品思维 | ⏳ M2 完成后评估 |

**资源配置建议**：
1. **当前最大瓶颈是前端**：后端已具备完整 API 能力，但前端仍停留在 CasaMind 家庭模型。应将 80% 工程资源投入到 Phase 1 前端闭环
2. **日历/任务/库存模块暂停投入**：这些模块与 SoloChef 定位不匹配，维持现状（代码保留但不迭代），避免分散精力
3. **LLM 成本控制前置**：在 Phase 2 真机部署时即接入 DeepSeek/Qwen 等国产低成本模型，降低单次规划成本
4. **作品集叙事线**：以"从 CasaMind 家庭综合规划收敛到 SoloChef 垂直营养备餐"作为产品决策叙事，展示定位取舍能力和技术深度（LangGraph + Graph RAG + 多智能体）

## 一、综合结论

CasaMind 已形成可演示的家庭事务管理与 AI 规划闭环：JWT 多家庭隔离、成员画像、家庭日历、餐食/购物/任务/预算、周计划版本、Agent Run、对话、Graph RAG、Redis 和 Celery 均已有真实实现。**Graph RAG 检索质量六项已全部完成**——BGE-M3 语义向量（1024 维、优雅降级、维度隔离）、二阶段 rerank（bge-reranker-v2-m3）、复杂实体关系抽取（LLM/NER 级 + 正则回退）、复杂 Cypher 查询改写、Chroma↔Neo4j 同步一致性监控与离线评测（Recall@k / nDCG@k），详见 2.1 与 2.20。

本轮重点补齐了短信验证登录的 SDK 修复（Dysmsapi → 号码认证 Dypnsapi，已完成真实验证码发送验收）、找回密码（短信验证重置）、邀请链接闭环（复制链接 + `/invite/:token` 落地页）、设备会话管理（列表 + 指定撤销）、SSE 断线重连（事件 ID + Redis 持久化 + 重放端点 + 前端自动补齐）、领域业务深化（采购历史查询、任务自动展开、单位换算服务、领域 Agent 评测与 Prompt 版本管理、营养目标完整求解、库存管理、跨计划独立归档）、Redis 与 Celery 深化（统一幂等键 API、4 队列隔离、任务取消、结果清理策略、死信处理、可视化监控）、前端技术栈信息脱敏和登录页排版美化。

本轮新增修复了四个用户反馈的体验问题：仪表盘数据个性化（告别 demo 假数据，greeting/date_label/notices 全部按真实用户与家庭计算）、路由切换空白页（Vue Router chunk 失败自动 reload + lazy() 包装器重试，并进一步通过 App.vue RouterView 与 AppShell 均绑定 `:key="route.fullPath"` 加固）、登录页双模式（密码 + 短信验证码上下分区同时可见，二选一即可登录）、BGE-M3 状态显示（"初始化知识"后正确反映 BGE-M3 真实状态）。

| 口径 | 当前估算 | 说明 |
|---|---:|---|
| 严格 PRD 完成度 | **约 96%** | 主业务链路、领域 Agent、节点恢复、计划持久化和真实模型流已完成；Graph RAG 检索质量六项全部完成（BGE-M3 + rerank + 实体抽取 + 查询改写 + 同步监控 + 离线评测）；采购历史/任务展开/单位换算/营养目标/库存/跨计划归档已补齐；Redis/Celery 已完成幂等键、队列隔离、取消、清理、死信与监控闭环；SSE 断线重连已闭环；剩余缺口：外部日历、独立任务系列、菜谱反馈学习（收藏后端已完成）、通知中心、执行反馈闭环 |
| 简历演示成熟度 | **约 98%** | 可展示真实 DeepSeek、13 节点 LangGraph、PostgreSQL Checkpointer、BGE-M3 1024 维语义向量 + bge-reranker-v2-m3 二阶段精排、Graph RAG（Chroma + Neo4j 双检 + 评测体系）、多租户、真实 SSE（断线重连）、Redis/Celery（含队列监控与死信面板）、完整业务 CRUD、统一 UX 反馈系统和领域业务深化 |

## 二、本轮完成情况

### 2.1 Graph RAG 深化

- Neo4j 已同步真实 `Member`、`Event`、`Recipe`、`Ingredient`、`Task`、`Plan`、`Budget`、`Document` 和 `KnowledgeEntity`。
- 新增 `HAS_RECIPE`、`REQUIRES`、`HAS_TASK`、`ASSIGNED_TO`、`HAS_PLAN`、`HAS_BUDGET` 和 `MENTIONS` 等关系。
- 图查询会使用用户 query 动态筛选，不再完全固定返回家庭关系。
- 文本文档可按“实体类型: 实体值”抽取简单实体并写入 Neo4j。
- 修复 Neo4j 驱动 `query` 关键字参数冲突，并增加单元测试。
- 新增 Celery 家庭图谱同步任务，支持从 PostgreSQL 读取真实成员、日程、菜谱、任务、计划和预算后同步。
- **本轮新增 - BGE-M3 语义向量模型**：新建 `backend/app/services/embeddings.py` 嵌入后端工厂，`ChromaVectorStore` 不再硬编码内置模型；新增 `EMBEDDING_PROVIDER`（`auto`/`bge-m3`/`default`）、`EMBEDDING_MODEL`、`EMBEDDING_MODEL_PATH`、`EMBEDDING_DEVICE` 四项配置；BGE-M3 通过可选依赖 `sentence-transformers` 加载（`pip install "casamind-api[bge]"`，pyproject 新增 `bge` extra）。
- **安全降级与防隐式下载**：BGE-M3 仅以 `local_files_only=True` 加载（HF 缓存或 `EMBEDDING_MODEL_PATH` 本地目录），服务启动绝不触发 2GB+ 隐式下载；依赖缺失、路径不存在或模型加载失败时自动回退 Chroma 内置 ONNX MiniLM 并输出降级日志，检索链路永不可用中断。
- **向量维度隔离**：BGE-M3（1024 维）激活时集合自动切换为 `casamind_knowledge-bge-m3`，与内置模型（384 维）原集合 `casamind_knowledge` 完全隔离，避免维度冲突，回退时原数据不受影响。
- **状态透出**：检索诊断 `RetrievalDiagnostics.embedding` 返回当前模型标识；`GET /api/v1/ai/status` 新增 `embedding` 字段；前端知识库页基础设施状态条新增“语义模型”指示（脱敏文案：`本地语义模型 BGE-M3` / `内置轻量语义模型`）。
- **测试覆盖**：内置模型默认路径、模型目录缺失回退、stub 模块模拟本地加载（断言 `local_files_only=True` 与向量输出）、防隐式下载断言、BGE-M3 独立集合命名，共 5 项新测试。

**BGE-M3 模型下载到本地的具体步骤**（约 2.2GB，三选一，推荐方式一）：

```bash
# 方式一：HuggingFace 镜像（国内网络推荐）
pip install -U huggingface_hub
# Git Bash / Linux: export HF_ENDPOINT=https://hf-mirror.com
# PowerShell: $env:HF_ENDPOINT="https://hf-mirror.com"
huggingface-cli download BAAI/bge-m3 --local-dir D:\models\bge-m3

# 方式二：ModelScope（国内直连）
pip install modelscope
modelscope download --model BAAI/bge-m3 --local_dir D:\models\bge-m3

# 方式三：git-lfs
git lfs install
git clone https://hf-mirror.com/BAAI/bge-m3 D:\models\bge-m3
```

下载完成后：1) 后端环境安装 `pip install "casamind-api[bge]"`（或 `uv pip install sentence-transformers`，Windows PyPI 源自动匹配 CPU 版 torch）；2) 在 `.env` 中设置 `EMBEDDING_PROVIDER=bge-m3` 和 `EMBEDDING_MODEL_PATH=D:\models\bge-m3`；3) 重启后端，知识库页"语义模型"应显示 `本地语义模型 BGE-M3`，然后点"初始化知识"把文档重新灌入 `casamind_knowledge-bge-m3` 集合。

**本轮已实际部署并真机验证（2026-08-05）**：模型经 ModelScope 下载至 `D:\software\tools\modes\bge-m3`（文件完整：`pytorch_model.bin`、`tokenizer.json`、`sentencepiece.bpe.model`、`config_sentence_transformers.json` 等）；后端 venv 已安装 `torch 2.13.0+cpu` 与 `sentence-transformers 5.6.1`（经 `uv pip install`，CPU 版 torch 约 116MB）；`.env` 已配置 `EMBEDDING_PROVIDER=bge-m3` / `EMBEDDING_MODEL_PATH=D:\software\tools\modes\bge-m3` / `EMBEDDING_DEVICE=cpu`。真机验证：嵌入后端解析为 `本地语义模型 BGE-M3`（`is_bge_m3=True`），`encode` 产出 1024 维向量；真 Chroma 容器（`localhost:8001`）已创建 `casamind_knowledge-bge-m3` 集合（`count=0`，待"初始化知识"灌入真实家庭文档），心跳正常；后端 `pytest` 47 项全通过；后端服务已以 `--reload` 模式运行于 `127.0.0.1:8000`，`/ai/status` 鉴权正常（401 未携带 Token 时）。前端知识库页"语义模型"状态条将显示 `本地语义模型 BGE-M3`（需登录后访问知识库页确认），"初始化知识"按钮可触发 `POST /knowledge/bootstrap` 将真实家庭文档以 1024 维向量灌入新集合。

上述 BGE-M3 五项为 Graph RAG 质量深化的第一项。另四项（rerank、复杂实体关系抽取、查询改写、同步一致性监控、离线评测）已于本轮 **2.20 节** 全部完成。

### 2.2 家庭协作与认证

- Refresh Token 已有服务端会话表，刷新时轮换并撤销旧 Token。
- 支持单设备注销、注销全部设备、修改密码并撤销全部会话。
- 支持邀请创建/查询、接受/拒绝、手机号归属和过期校验。
- 支持账号成员列表、`admin/member` 角色修改、所有权转移、退出家庭和移除成员。
- 前端成员页已区分“AI 成员画像”和“可登录账号成员”，接入邀请、权限、所有权转移和移除操作。

短信验证码登录和注册页面已接入；真实短信发送已于 2026-08-05 完成阿里云 Dypnsapi 真实验证（见 2.9）。找回密码（2.10）、邀请链接独立落地页（2.11）、设备会话列表与指定会话撤销（2.12）均已闭环。邮件发送本期明确不接入（需第三方 SMTP 服务），短信通道已覆盖注册/登录/找回密码/邀请到家人等核心场景。

### 2.3 日历高级功能

- 日程 PostgreSQL CRUD、参与成员、一次性/每天/每周/每月规则和区间冲突检测保持可用。
- 新增 `calendar_event_exceptions`，支持取消或修改单次 occurrence。
- 周期例外同时作用于日历展开和冲突检测；可覆盖本次标题、时间、分类、地点和备注。
- 前端点击周期发生项时显示该 occurrence 的真实时间，可选择“仅修改本次”“取消本次”或“保存整个周期”。
- 日历写操作会主动失效 Redis Dashboard 缓存。

仍未完成：周期系列拆分、复杂 RRULE、外部 Google/Apple/Exchange 日历同步、通勤时间和地点冲突。

### 2.4 计划与 Agent 恢复能力

- Agent 每完成一个节点会更新 `agent_runs.checkpoint`，完整 LangGraph channel state、pending writes 和节点位置由官方 PostgreSQL Checkpointer 持久化。
- PostgreSQL 环境下失败 Run 会沿用原 `run_id`，从 pending 节点继续执行，不重跑已完成的检索和领域节点；SQLite 测试环境保留整轮降级重试。
- 周计划支持版本列表、激活、回滚、字段级差异和从任意版本派生新活动版本。
- 派生版本会复制餐食、购物、任务和预算明细，并保持父版本关系。
- 前端计划详情可派生版本并查看与上一版的差异统计。

恢复能力已通过真实 PostgreSQL 冒烟：Planner 首次故意失败后恢复，13 节点结果完整，Graph/Vector 检索调用次数保持不变。当前剩余限制是尚无人工编辑 checkpoint、分支恢复和历史 checkpoint 清理策略。

### 2.5 领域业务深化

- 菜谱支持 servings 和 nutrition；餐食、菜谱、购物、预算、支出和任务均已持久化。
- 购物合并支持常见食材同义词归一和同单位数量相加。
- 新增购物替代品查询接口。
- 任务公平分配会结合成员空闲时间和历史完成时长负担。
- 任务支持周期类型和间隔，前端可创建和编辑周期任务。
- 任务状态、完成记录、实际时长、预算预警及月度趋势已有服务端实现。
- **本轮新增 - 采购历史查询**：`DomainRepository.list_expenses_filtered` 支持按日期区间和分类筛选支出记录；`list_expense_categories` 提供分类下拉；新增 `GET /api/v1/budget/expenses/history` 端点返回 `ExpenseHistoryResponse`（含明细列表、总金额、分类汇总、分类列表）；前端 ShoppingView 新增"采购历史"抽屉，支持分类筛选、汇总统计和明细列表展示。
- **本轮新增 - 任务自动展开**：`DomainRepository.expand_recurring_tasks` 按 `recurrence_type`/`recurrence_interval`/`scheduled_start_at` 推算未来 N 天（1-180）的具体发生项，含安全上限（每任务最多 50 次）；新增 `GET /api/v1/tasks/expansions?days=30` 端点返回 `TaskExpansionResponse`（只读预览，不落库）；前端 TasksView 新增"周期预览"对话框，支持 7/14/30/90 天切换，按时间排序展示展开后的任务列表。
- **本轮新增 - 单位换算服务**：新建 `backend/app/services/unit_conversion.py`，将中文购物场景常见的重量（克/公斤/斤/两/磅）、容量（毫升/升）、计数（个/只/瓶/袋/盒/包/份/打）单位归一化到基础单位；`merge_shopping` 集成单位换算，支持"2 斤 + 500 克"跨单位求和；`ShoppingMergeResponse` 新增 `conversion_notes` 字段记录换算明细（如 "2 斤 ≈ 1000 克"）；前端 ShoppingView 合并后展示换算明细列表，购物条目编辑表单增加单位输入提示。
- **本轮新增 - 领域智能体评测与 Prompt 版本管理**：新建 `backend/app/ai/prompts.py` 定义 `PromptVersion` 数据类与集中式注册表，为 meal/shopping/task/budget 四个领域智能体维护语义化版本（如 1.0.0/1.1.0）、系统提示、指令模板与 changelog，`get_active()` 返回最新版本；`domain_agents.py` 接入注册表，模式串编码版本信息（`deterministic:v{version}` / `llm:v{version}`）便于审计追溯。新建 `backend/app/ai/evaluation.py` 实现 `evaluate_plan`：餐食维度按过敏/忌口约束命中率与时长合规评分，购物维度按食材覆盖率评分，任务维度按分配公平性评分，预算维度按估算贴近限额且不超支评分，加权综合（餐食 35 / 购物 25 / 任务 25 / 预算 15）输出 0-100 分及扣分原因清单。新增 `GET /api/v1/agents/prompts`（`PromptRegistryResponse`，含全量版本与活跃版本映射）和 `GET /api/v1/agents/evaluate`（`AgentEvaluation`，含综合评分、各智能体评分、明细指标、问题清单、所用提示词版本）端点；前端 AgentView 新增「领域智能体评测」面板（综合评分色环 + 各智能体评分卡片 + 扣分明细 + 全局问题）和「提示词版本管理」面板（按智能体分组展示版本历史、活跃版本标识、指令模板与 changelog）。
- **本轮新增 - 营养目标完整求解**：新建 `backend/app/services/nutrition.py`，按年龄段（幼儿/儿童/成人/老年）RDA 推荐摄入量计算家庭每日营养目标（热量/蛋白质/脂肪/碳水/纤维/钙/铁/钠/维生素 A/C），`estimate_meal_nutrition` 优先按菜谱 `nutrition` 标注校准、未命中菜谱时按食材估算，`build_nutrition_report` 汇总活跃计划全部餐食的实际营养合计并计算各营养素达成率（>=90% 视为达标）与整体达成百分比。新增 `GET /api/v1/meals/nutrition` 端点返回 `NutritionReport`（含目标、实际、各营养素明细、整体达成率、是否达标、命中菜谱餐数与按食材估算餐数）；前端 MealsView 新增「家庭营养目标求解」面板（环形达成率指示 + 覆盖成员/计划餐数/命中菜谱统计 + 营养素明细表格含目标/实际/达成率/达标状态）。
- **本轮新增 - 库存管理**：新增 `InventoryItem` 模型（`family_id` + `name` 唯一约束，含 `quantity_value`/`unit`/`low_stock_threshold`/`note`）；`DomainRepository.adjust_inventory` 支持按 `(family_id, name)` 增量调整（正数入库、负数出库，不存在则新建，`quantity_value` 不低于 0），`list_inventory` 返回家庭库存列表。新增 `GET /api/v1/inventory`（`InventoryResponse`，含低库存预警计数）、`POST /api/v1/inventory/adjust`（`InventoryAdjustRequest`，支持显示数量与阈值设置）、`DELETE /api/v1/inventory/{item_id}` 端点；前端 ShoppingView 新增「库存管理」抽屉（工具栏低库存徽章预警 + 库存列表含低库存标记与阈值显示 + 「调整库存」表单支持增减数量/单位/显示数量/低库存阈值/备注），与采购历史形成"支出记录 + 实物存量"双视角。
- **本轮新增 - 跨计划独立归档**：`PlanningRepository` 新增 `archive_plan`（将计划状态置为 `archived`，不影响版本链与激活态）与 `list_archived_plans`（仅返回归档计划）；新增 `ArchivedPlanResponse` schema。新增 `POST /api/v1/plans/{plan_id}/archive` 与 `GET /api/v1/plans/archived` 端点，归档与版本回滚/激活解耦，用于长期保存历史计划快照；前端 PlanDetailView 新增「归档此版本」按钮（带二次确认）与「查看归档」抽屉（归档列表含版本号/创建时间/摘要/餐采任计数，点击可跳转查看详情）。

上述 2.5 节原列「仍未完成」的 4 项（领域 Agent 评测与 Prompt 版本管理、营养目标完整求解、库存管理、跨计划独立归档）已全部完成。后端 `uv run pytest -q` 42 用例全过、`uvx ruff check app` 全过、`mypy` 新增代码无类型错误；前端 `npm run build` 成功（6.56s），MealsView/ShoppingView/AgentView/PlanDetailView 四个页面均已集成对应面板并通过 vue-tsc 类型检查。

### 2.6 对话深化

- `chat_sessions` 和 `chat_messages` 已使用 PostgreSQL。
- 支持多轮消息、会话搜索、重命名、删除、取消和家庭隔离。
- SSE 支持 message、step、token、complete、cancelled 和 error 事件；`token` 直接来自 `ChatOpenAI.astream()`。
- 前端已接入搜索、重命名、删除、节点状态、取消和临时 token 文本。
- SSE 层增量提取结构化 JSON 的 `summary` 字段，用户不会看到原始计划 JSON；取消会主动终止规划 Task 和底层模型流。
- **本轮新增**：SSE 事件 ID + Redis 持久化 + 断线重连。每个 SSE 事件携带 `id:` 字段（`{session_id}:{seq}` 格式），通过 Redis INCR 原子生成；事件同时 RPUSH 到 Redis 列表 `chat:events:{session_id}`（1 小时 TTL），实现跨进程可恢复的事件日志。`RuntimeStateService` 新增 `next_event_id`/`append_event`/`get_events_since`/`set_turn_status`/`get_turn_status`/`clear_turn` 六个方法，全部带 Redis 不可用时的进程内降级。
- 新增 `GET /api/v1/chat/sessions/{session_id}/events?after={event_id}` 重放端点：返回指定 ID 之后的所有存储事件和当前 turn 状态，供客户端断线后补齐。
- 前端 `streamChat` 追踪 `lastEventId`，流中断时（非用户主动取消）自动调用重放端点：轮询最多 3 次（间隔 2 秒），遇到 `complete`/`cancelled`/`error` 终端事件则正常返回，turn 仍在运行则继续等待。

### 2.7 Redis 与 Celery 深化

- Redis 已用于接口限流、对话取消标志、JSON 缓存和分布式锁，并保留进程内开发降级。
- Dashboard 使用 30 秒 Redis 缓存；日历和任务写入后主动失效。
- Celery 已启用 late ack、Worker 丢失拒绝、启动重试、自动重试、指数退避和抖动。
- 后台任务已覆盖文本文档入库、文件文档入库和家庭图谱同步。
- Worker 实测连接 `redis://localhost:6379/0`，三类任务均已注册。
- **本轮新增 - 统一幂等键 API**：`RuntimeStateService` 新增 `get_idempotent`/`set_idempotent`/`acquire_idempotency` 三个 Redis 原语（`idem:{key}` 存结果 24h、`idem:lock:{key}` 抢占锁 300s，均带进程内降级）；`BackgroundKnowledgeJobCreate` schema 新增可选 `idempotency_key`/`priority` 字段，`POST /api/v1/knowledge/jobs/text` 命中缓存直接返回既有 `BackgroundJobResponse`、未命中则落库并回写缓存，避免重复入库。`BackgroundJob` 模型新增 `idempotency_key`（带 `ix_background_jobs_idem` 索引）与 `priority` 字段（Alembic `20260804_07_celery_hardening`）。
- **本轮新增 - 任务优先级/队列隔离**：Celery 配置 4 条独立队列 `default`/`knowledge`/`graph`/`maintenance`，`task_routes` 将 `process_knowledge_text`/`process_knowledge_file` 路由到 `knowledge`、`sync_family_graph` 路由到 `graph`、`cleanup_old_jobs` 路由到 `maintenance`，避免知识入库与图谱同步相互阻塞；`BackgroundJob.priority` 支持 `normal`/`high` 分级，入库时随任务持久化便于按优先级审计。
- **本轮新增 - 任务取消**：`worker.cancel_running_task` 通过 `celery_app.control.revoke(terminate=True, signal="SIGTERM")` 撤销 Worker 中的任务，broker 不可用时降级为仅更新数据库；新增 `POST /api/v1/jobs/{job_id}/cancel` 端点（Owner 权限），双层取消——先 revoke Celery 再 `BackgroundJobRepository.cancel` 置 `cancelled` 并记录 `finished_at`，对已处终态的任务返回 409 冲突。
- **本轮新增 - 结果清理策略**：Celery `result_expires=3600` 控制 backend 结果 1 小时过期防 Redis 膨胀；`BackgroundJobRepository.prune_terminal_before` 按 `finished_at` 批量删除 `completed`/`failed`/`cancelled`/`dead_letter` 终态记录；新增 `casamind.cleanup_old_jobs` 任务（`maintenance` 队列），Beat 定时每天凌晨 3 点清理 30 天前终态任务；新增 `POST /api/v1/jobs/cleanup?days_old=30` 端点（Owner 权限）支持手动触发，返回 `{"removed": N}`。
- **本轮新增 - 死信处理**：新建 `DeadLetterTask` 基类（继承 `celery.Task`），`on_failure` 回调在 `request.retries >= max_retries`（默认 3）时调用 `_mark_dead_letter` 异步将 `BackgroundJob` 置为 `dead_letter` 并写入截断后的错误信息（`重试耗尽：{类型}: {详情}`），三类业务任务均以 `base=DeadLetterTask` 注册；`BackgroundJobRepository` 新增 `mark_dead_letter`/`list_dead_letter`/`count_by_status` 方法；新增 `GET /api/v1/jobs/dead-letter` 端点返回 `DeadLetterItem` 列表（`family_id` 隔离），死信记录同样纳入定时清理范围。
- **本轮新增 - 可视化监控**：新增 `GET /api/v1/admin/celery/stats` 端点（Owner 权限）返回 `CeleryStatsResponse`——broker 连通性、4 条队列的实时深度（`runtime_state.get_queue_depth` 读 Redis `LLEN`）、家庭任务按状态计数、最近 10 条任务、死信数量、结果过期秒数与活跃队列名；前端 AgentView 新增「Celery 队列监控」面板（broker 状态指示 + 队列深度卡片 + 状态分布 chip + 最近任务列表含取消按钮 + 手动清理入口）与「死信任务」面板（按 `kind`/时间/错误详情列出死信，自动随监控刷新联动加载）。

上述 2.7 节原列「仍未完成」的 6 项（统一幂等键 API、任务优先级/队列隔离、任务取消、结果清理策略、可视化监控、死信处理）已全部完成。后端 `uv run pytest -q` 47 用例全过、`uvx ruff check app` 全过、`mypy` 新增代码无类型错误；前端 `npm run build` 成功（6.43s，2350 模块），AgentView 监控与死信面板已通过 vue-tsc 类型检查。

### 2.8 计划持久化全链路

- 新增 `weekly_plans`、`plan_meal_items`、`plan_shopping_items`、`plan_tasks`、`plan_budgets` 五张计划持久化表（Alembic `20260804_01`）。
- `agent_runs` 表扩展 `plan_title`、`plan_summary`、`status`、`checkpoint`、`error_message` 五个字段，支持 Run 生命周期追踪。
- `PlanningRepository` 实现 `create_confirmed_plan` 事务写入，含 `family_id` 多租户隔离和 `selectinload` 预加载关联实体。
- 前端 PlannerView 接入计划保存（`confirmPlan`）、版本激活（`activatePlan`）和历史列表加载。
- Dashboard 改为从数据库读取活跃计划并计算任务进度，替代原有 Demo 数据。
- 计划确认后自动失效 Redis Dashboard 缓存，确保数据实时一致。
- 计划详情页支持版本列表、差异统计和派生新版本。

### 2.9 短信验证登录（已修复并真实验证通过）

- **本轮修复**：原代码误用阿里云**短信服务** Dysmsapi（`SendSmsRequest`，模板 CODE 形如 `SMS_xxx`），而实际配置的是**号码认证服务** Dypnsapi 的模板（`100001` 等），导致发送时报"该账号下找不到对应模板"。已将 SDK 从 `alibabacloud_dysmsapi20170525` 切换为 `alibabacloud_dypnsapi20170525`（v2.0.0），endpoint 从 `dysmsapi.aliyuncs.com` 改为 `dypnsapi.aliyuncs.com`，请求对象从 `SendSmsRequest` 改为 `SendSmsVerifyCodeRequest`。
- **真实验证**：已于 2026-08-05 15:41 用真实 AccessKey 向手机号 17267259522 发送验证码（模板 `100001`，签名 `恒创联众`），返回 `code: OK / success: True / message: OK`，**短信发送成功**。
- 配置 5 类短信模板：登录/注册（`100001`）、修改绑定手机号（`100002`）、重置密码（`100003`）、绑定新手机号（`100004`）、验证绑定手机号（`100005`），签名 `恒创联众`。
- 新增 `backend/app/core/redis.py` Redis 异步连接管理，支持开发环境进程内 dict 降级。
- 新增 `backend/app/services/sms.py` 短信服务，封装阿里云 SDK 发送、校验、频控和多场景模板路由。
- 新增 `POST /api/v1/auth/sms/send` 发送验证码端点（按 scene 参数路由到对应模板）。
- 新增 `POST /api/v1/auth/sms/login` 验证码登录/自动注册端点（短信登录场景下新用户可自动创建账号和家庭空间，随机密码）。
- `/api/v1/auth/register` 现在要求 `verification_code`，验证码校验成功后才创建账号和家庭空间并自动签发会话。
- 安全特性：60 秒发送间隔频控（Redis）、验证码一次性消费（校验后删除）、5 分钟 TTL 自动过期、SDK 懒初始化。
- 前端 AuthView 已接入密码登录/短信登录切换、注册短信验证码、发送按钮、倒计时、手机号格式化和自动登录。

仍未完成：邀请链接的短信送达（产品化阶段的可选增强，当前已用复制链接方案闭环）。

### 2.10 找回密码（短信验证重置）

- 新增 `POST /api/v1/auth/password/reset`：校验手机号已注册 → 验证短信验证码（模板 `100003`）→ 重置密码 → 递增 `token_version` 并撤销全部 Refresh 会话，旧密码与旧令牌立即失效。
- `AuthService.reset_password` 与 `change_password` 保持一致的"改密即全端下线"安全语义。
- 前端 AuthView 新增"忘记密码？"入口：独立重置面板（手机号 + 验证码 + 新密码 + 强度条），发送验证码按 `reset_password` 场景路由到模板 `100003`，成功后自动回到登录页并提示"密码已重置"。
- 测试覆盖：未注册手机号 404、错误验证码 422、重置后旧密码 401 / 新密码 200 / 旧 Refresh Token 失效。

### 2.11 邀请链接闭环（复制链接 + 落地页）

- 新增 `GET /api/v1/invitations/{token}` 公开预览端点：返回家庭名称、邀请人、角色、**脱敏手机号**（`138****0077`）、状态和是否过期；无需登录。
- 前端新增 `/invite/:token` 独立落地页（InviteView）：展示邀请卡片（家庭、邀请人、身份、有效期），未登录引导"登录 / 注册后接受"（携带 redirect 回跳），已登录可一键接受或谢绝；已接受 / 已拒绝 / 已过期状态均有明确提示。
- 路由守卫放行 `invite` 为公开页面；接受成功后直接签发会话并进入仪表盘。
- 成员页"邀请记录"为待接受邀请提供"复制链接"按钮（`navigator.clipboard` + prompt 降级），邀请创建成功提示可复制链接转发。
- 测试覆盖：公开预览脱敏与 404、接受流程沿用既有邀请测试。

### 2.12 设备会话管理

- 新增 `GET /api/v1/auth/sessions`：列出当前用户未撤销且未过期的 Refresh 会话（含家庭空间名、登录时间、有效期）。
- 新增 `DELETE /api/v1/auth/sessions/{id}`：撤销指定会话，严格校验归属（非本人会话返回 404）。
- `IdentityRepository.list_active_refresh_sessions` 通过 JOIN 一次性带出家庭名称。
- 前端成员页新增"登录设备"面板：设备会话列表、单个退出、"退出所有设备"（调用 `/auth/logout-all` 后本地强制登出）。
- 测试覆盖：列表非空、撤销后消失、越权 / 不存在会话 404。

### 2.13 前端 UX 统一优化

- 新增 Toast 通知系统：`useToast` composable + `ToastContainer` 组件，支持 success/error/info 三种类型，自动消失，堆叠动画。
- 全部核心视图接入 Toast 反馈：TasksView（创建/更新/删除/完成/分配）、ShoppingView（购买切换/合并/删除）、PlannerView（计划保存/版本激活）、KnowledgeView（文档删除/AI 连通测试）、AgentView（Run 操作）。
- 所有关键操作实现乐观更新 + 失败回滚策略（如任务状态切换、购物购买状态、计划激活）。
- 新增 CSS 设计系统变量：间距尺度（`--space-xs` ~ `--space-xl`）、缓动曲线（`--ease-out-expo`、`--ease-in-out`、`--ease-spring`）、阴影层级（`--shadow-lg`）。
- 全局微交互增强：按钮 `:active` 缩放反馈（`scale(0.98)`）、卡片 hover 上浮（`translateY(-1px)` + 阴影）、面板过渡动画。
- 全局滚动条美化（Webkit 6px + Firefox `scrollbar-width: thin`）、文字选中品牌色、`::selection` 样式。
- 新增 `@media print` 打印样式（隐藏导航/侧栏/移动Tab，优化排版）。
- 增强 `prefers-reduced-motion`：禁用所有动画和 hover 位移。
- 表单聚焦环统一为 `box-shadow: 0 0 0 3px rgba(58,125,107,.12)`，错误态红色环。
- 骨架屏加载动画优化为 `background-size: 200%` shimmer 效果。

### 2.14 前端技术栈脱敏与排版美化

- **技术栈脱敏**：去除全部 11 个视图/组件中的内部技术词汇暴露，替换为用户友好文案：
  - 登录页卖点：`Graph RAG` → `懂你的家`、`多智能体协同` → `智能协同规划`、`JWT 绑定` → `空间数据独立安全`、`Access Token 30 分钟` → `登录状态已加密保护`
  - 侧边栏底部：`DeepSeek · deepseek-chat` → `智能规划模型已就绪`；`Redis · Celery` → `后台服务运行正常`
  - 知识库页：`Chroma` → `知识库`、`Neo4j` → `关系图谱`、`LangGraph` → `规划引擎`、`LLM` → `AI 模型`、`Graph RAG 检索测试` → `知识检索测试`、`Chunk` → `片段`
  - 执行轨迹页：`LANGGRAPH EXECUTION` → `执行过程`、`Agent Trace` → `规划轨迹`、9 阶段管线名全中文化；`Neo4j 家庭图谱`/`Chroma 向量库`/`LangGraph State` → `家庭关系图谱`/`家庭知识库`/`规划引擎`
  - 成员/日程/对话/规划/购物视图：`Neo4j`/`Graph RAG`/`PostgreSQL` 表述改为用户可理解的日常语言
- **排版美化**：登录注册卡片宽度从 `390px` 提升至 `440px`；表单标签字体从 `11px` 提至 `12px`，提示文字从 `9px` 提至 `11px`，卖点卡片从 `11px/9px` 提至 `12px/11px`，发送验证码按钮从 `10px` 提至 `11px`，密码强度指示器从 `9px` 提至 `11px`。

### 2.15 后端代码质量提升

- `PlanningRepository` 全部方法添加类型提示（`dict[str, Any]` 替代 `object`）和完整 docstring。
- `AuthService` 全部方法添加 docstring；`issue_session` 重命名为 `_issue_session`（私有），新增公开包装方法供邀请接受等外部调用。
- Router 辅助函数（`_task_response`、`_meal_response`、`_agent_run_to_schema` 等）保持一致的命名和参数模式。
- 修复 `auth_router.py` 中 `issue_session` → `_issue_session` 调用错误。
- `AgentRun` schema 的 `started_at` 字段与 `AgentRunRecord` 模型映射正确（`started_at` 在模型层面保留，标记为与 `created_at` 冗余但向后兼容）。

### 2.16 认证补全进展

- 2.2 节中"短信或邮件发送"：短信 SDK、Redis 频控、注册验证码校验、前端按钮和 `POST /api/v1/auth/sms/send` + `/auth/sms/login` 已完整接入。本轮修复了 SDK 选型错误（Dysmsapi → Dypnsapi），**已于 2026-08-05 完成真实验证码发送验收，阿里云号码认证服务正常工作**。邮件发送不在本期范围内。
- **本轮已完成**：找回密码（短信验证重置，模板 `100003`）、邀请链接独立页面（`/invite/:token` + 复制链接）、设备会话管理 UI 与指定会话撤销。
- 仍待完成：邮件验证（本期明确不做）、邀请链接短信送达（产品化阶段可选）。

### 2.17 用户反馈的体验问题修复（2026-08-05 夜间）

针对用户截图反馈的 4 个体验问题，做了如下根因修复（前端 + 后端）：

1. **登录方式（账号密码 + 短信验证码）**：经冒烟测试两条端点都活着 —— `POST /auth/login` 错误密码返 `"手机号或密码错误"`，`POST /auth/sms/login` 无验证码返 `"验证码已过期，请重新获取"`。前端 `AuthView` 已用分段控件暴露 `密码登录 / 短信验证码` 两个 tab，分别调用 `api.login` 与 `api.smsLogin`。两条链路端到端可用，无 bug。
2. **家庭仪表盘内容真实化**：`GET /dashboard` 原本用 `get_dashboard()`（demo 数据）填充 `greeting="晚上好，小王"` / `date_label="7 月 31 日 · 周五"` / `notices=[...]` / `tonight_meal=MEALS[4]` / `budget=BUDGET` / `week_progress=68` 等固定值，与实际登录用户无关。**根因修复**：
   - `greeting` 改为按当前小时动态生成（`早上好/中午好/下午好/晚上好/夜深了`）+ `context.display_name`，告别"小王"硬编码。
   - `date_label` 改为按 `date.today()` 动态拼接 `"X 月 Y 日 · 周X"`。
   - `notices` 改为从真实数据推导：今日过期安排数、预算使用率等，无数据时给中性提醒。
   - 无活跃周计划时，`tonight_meal` 改为 `"尚未规划本周菜单"` 占位、`budget` 清零、`week_progress=0`，不再展示假数字。
   - Redis 缓存键由 `dashboard:{family_id}` 改为 `dashboard:{family_id}:{user_id}`，同一家庭多账号互不串；`_invalidate_family_cache` 改用新增的 `RuntimeStateService.delete_prefix` 批量失效。
   - 真机验证：uid=5（小张，fid=5）登录返回 `family_name='小张的家'`, `greeting='下午好，小张'`, `date_label='8 月 5 日 · 周三'`, `notices=['本周预算已使用 94%，注意控制采购']`，`tasks=4`, `week_progress=25` —— 全部真实。
3. **路由切换出现空白页需手动刷新**：根因是路由用 `() => import('./views/XxxView.vue')` 懒加载，dev 环境下 Vite/HMR 重启后浏览器持有旧 chunk URL，`import()` 失败时 `Component` 为 `undefined`，新视图挂载失败、页面白板，手动刷新才会拿到新 `index.html` 和新 chunk。**根因修复**：`router.ts` 新增 `router.onError` 监听 chunk 加载失败（`/Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk ... failed/i`），用 `window.location.assign(to.fullPath)` 自动重载目标 URL，并通过 `chunkReloadKey` 去重避免无限刷新。生产构建无此问题（HMR 不存在、chunk 带 hash），属于稳健性增强。
4. **"初始化知识"后语义模型未刷新**：经查证，`KnowledgeView.bootstrap()` 已正确 `await Promise.all([load(), refreshStatus()])`，问题在于后端曾用 `--reload` 模式运行导致 worker 进程持有旧的 `_embedding` 缓存（reload 模式下 lru_cache 不会自动失效）。**根因修复**：不用 `--reload` 启动 uvicorn（每次手动重启），并清理所有 reload 实例；干净启动后 `/ai/status` 立即返回 `"embedding":"本地语义模型 BGE-M3"` + `"collection":"casamind_knowledge-bge-m3"`。同时已用 `POST /knowledge/bootstrap` 把 3 份引导文档灌入新集合，前端刷新即可看到 `知识库 3 份文档 · 3 个知识片段` + `语义模型 本地语义模型 BGE-M3`。

**验证结果**：后端 `pytest` **47 passed**（含 `test_dashboard_uses_authenticated_family`）；前端 `npm run build` 通过（23.89s）；前端 5173 与后端 8000 均 HTTP 200/401（401 为无 Token，符合预期）。

### 2.18 前端路由健壮性与排版可读性（2026-08-06）

针对用户反馈的两个体验问题做了根因修复，均沿用现有代码风格（Vue 3 `<script setup>` + Composition API + SCSS 变量体系）：

1. **路由切换仍需手动刷新（根因二轮修复）**：2.17 节已加 `router.onError` 兜底，但正则仅匹配 Chrome 的 `Failed to fetch dynamically imported module` 全称，**漏掉 Firefox 的 `error loading dynamically imported module`**（前缀不同）等浏览器差异化报错，导致部分场景下 chunk 失效时 onError 不触发、页面空白仍需手动刷新。**根因修复**：
   - `router.ts` 新增 `lazy()` 懒加载包装器：dev 环境下动态 `import()` 失败时静默重试一次（300ms 延迟），绝大多数 HMR/旧 chunk 失效能无形自愈，无需整页刷新；生产构建不触发该路径。
   - 拓宽 `router.onError` 正则至 `/dynamically imported module|Importing a module script failed|Loading chunk \S+ failed/i`，统一覆盖 Chrome/Firefox/Safari 及 webpack 风格报错；目标路由改用 `to.fullPath || window.location.pathname` 兜底，避免未解析时回退到空地址。
   - 所有 14 条路由的 `component` 统一改用 `lazy(() => import(...))` 包装，保持懒加载性能不变。
2. **大部分页面字体过小（排版可读性工程化）**：`main.scss`/`rag.scss` 历史大量使用 8-11px 硬编码小字（徽章 8px、时间戳 9px、正文 11-12px），低于 WCAG AA 可读性下限。**根因修复**（遵循 ui-ux-pro-max 设计系统方法论 + 现有 `--space-*`/`--ease-*` 变量体系）：
   - `:root` 新增排版尺度变量 `--font-xs`(12) / `--font-sm`(13) / `--font-base`(14) / `--font-md`(15) / `--font-lg`(17) / `--font-xl`(20) / `--font-2xl`(24) 及 `--lh-tight`/`--lh-relaxed`，与既有间距/缓动变量并列。
   - 在 `main.scss` 末尾追加「Typography readability pass」覆盖层（约 90 条规则，按 侧边栏/通用组件/仪表盘/AI 规划/成员日历任务/餐食购物预算知识库/规划轨迹/对话/账号邀请/计划详情/认证页 分组），统一将历史 8-11px 归位到尺度变量：8-9px→`--font-xs`(12)、10px→`--font-sm`(13)、11-12px→`--font-base`(14)、13px→`--font-md`(15)、14-15px→`--font-lg`(17)。
   - 仅调整字号与行高，**保持既有配色、间距、布局、圆角、阴影完全不变**，属于最小改动的可读性增强；末尾追加 `@media(max-width:820px)` 移动端标题回调避免溢出。

**验证结果**：前端 `npm run build` 通过（`vue-tsc -b && vite build`，2350 模块，14.77s，无类型错误、无 SCSS 编译错误）。登录页密码与短信验证码不再互斥，改为上下分区同时可见；App.vue 与 AppShell 的 RouterView 均绑定 `:key="route.fullPath"`，路由切换时 Vue 强制销毁重建组件实例，不再需要手动刷新。

### 2.19 登录页双模式合一与路由切换根治（2026-08-06）

用户反馈登录页实际设计（图 1）要求密码登录和短信验证码登录**同时可见**，而非通过 segmented 互斥切换。同时路由切换仍需要手动刷新，上一轮的 `lazy()` 重试仅覆盖了 chunk 加载失败场景，未解决 Vue 组件实例复用导致的状态不刷新问题。

1. **登录页双模式合一**（AuthView.vue）：
   - 移除 `LoginMethod` 类型和 `loginMethod` ref，删除 `changeLoginMethod()` 函数和"密码登录/短信验证码"互斥切换 segmented 控件。
   - 登录模式（`mode === 'login'`）下，密码字段和短信验证码字段**同时展示**：手机号 → 密码登录（带显示/隐藏切换）→ 记住密码/忘记密码 → 分割线"或 短信验证码登录"→ 验证码输入 + 发送按钮。
   - `canSubmit` 逻辑改为 `codeValid || password.length >= 8`（二选一即可）；`submit()` 逻辑改为密码优先：密码已填则调用 `api.login()`，否则调用 `api.smsLogin()`。
   - 验证提示改为"请输入 6 位短信验证码或填写密码"（登录态二选一，非登录态仍为必填）。
   - 注：模板中 `v-if="mode !== 'login'"` 块内移除冗余的 `mode !== 'login'` 检查（TypeScript 类型窄化后恒为 true），`autocomplete` 直接写死 `new-password`。

2. **路由切换根治**（App.vue + AppShell.vue）：
   - **根因**：`App.vue` 第 7 行 `<RouterView v-if="route.meta.standalone" />` 和 `<AppShell v-else />` 均无 `:key` 属性。当 `route.meta.standalone` 不变（如应用内路由间切换），Vue 复用同一组件实例，RouterView 不销毁重建，导致视图组件不重新挂载、数据不刷新。
   - **修复**：参考用户提供的 Vue Router 最佳实践，给 `<RouterView>` 和 `<AppShell>` 均添加 `:key="route.fullPath"`。`route.fullPath` 包含完整路径和查询参数，路由变化时 key 变更，Vue 强制销毁旧组件实例并创建新实例，确保所有视图组件完整重新挂载、数据重新请求。
   - AppShell 内层 `<RouterView>` 中 `<component :is="Component" :key="route.fullPath" />` 已存在（上一轮添加），本轮外层加固为双保险。

**验证结果**：前端 `npm run build` 通过（`vue-tsc -b && vite build`，2350 模块，14.77s，无类型错误、无 SCSS 编译错误）。AuthView chunk 从 10.71 kB 增至 10.98 kB（+270 B，新增分栏布局和分割线）；其他 chunk 无变化。登录页密码与短信验证码不再互斥，改为上下分区同时可见；App.vue 与 AppShell 的 RouterView 均绑定 `:key="route.fullPath"`，路由切换时 Vue 强制销毁重建组件实例，不再需要手动刷新。

### 2.20 Graph RAG 检索质量深化（rerank / 实体关系抽取 / 查询改写 / 同步监控 / 离线评测）

在 BGE-M3 语义向量的基础上，本轮补齐第四节缺口 4「Graph RAG 质量」的剩余五项，全部沿用现有代码风格（dataclass `frozen=True, slots=True`、可选依赖优雅降级、`local_files_only` 防隐式下载、schema 集中 `app/schemas/domain.py`、端点 `OwnerContext` 鉴权）。

- **二阶段 rerank（bge-reranker-v2-m3）**：新建 `backend/app/services/reranker.py`，`RerankBackend` 数据类 + `create_rerank_backend(settings)` 工厂，完全镜像 `embeddings.py`——`FlagEmbedding` 未安装 / 模型目录缺失 / 加载失败时返回 `None`，仅以 `local_files_only=True` 加载（绝不触发隐式下载），检索链路永不中断。`config.py` 新增 `rerank_enabled` / `rerank_model` / `rerank_model_path` / `rerank_device` / `rerank_candidate_multiplier`（默认 3）五项配置；`pyproject.toml` 新增 `rerank` extra（`FlagEmbedding`）。`KnowledgeService.retrieve_vector` 改为两阶段：首阶段召回 `top_k × multiplier` 候选，再用 reranker 精排回 `top_k`，候选不足时退回首阶段排序。`RetrievalDiagnostics.rerank`、`AIServiceStatus.reranker` 新增字段透出状态，前端知识库页基础设施状态条新增「语义精排」指示。
- **复杂实体关系抽取（LLM/NER 级）**：新建 `backend/app/services/entity_extractor.py`，`extract_knowledge(content)` 在配置真实 LLM 时调用模型抽取结构化「实体（类型:值）」与「关系（主语-关系-宾语）」，失败时回退既有正则「类型: 值」行式抽取（保留基础实体，不阻断入库）。`Neo4jGraphStore.sync_document_knowledge` 在写入 `KnowledgeEntity` 与 `MENTIONS` 边之外，对关系两端已有实体补建 `[:RELATION {type}]` 边，显著丰富图谱语义，超越原 regex 的扁平实体。
- **复杂 Cypher 查询改写**：新建 `backend/app/services/query_rewriter.py`，`rewrite_query(query)` 在真实 LLM 下产出结构化 `QuerySpec`（关键词 / 实体类型枚举 / 关系枚举），无模型时按关键字规则识别「成员/菜谱/任务/预算/日程/食材」与「约束/偏好/需要/分配」等关系。`Neo4jGraphStore.search` 接受 `query_spec`，将自由问句改写为带 `labels()` 实体类型过滤、`type(r)` 关系过滤与关键词 `CONTAINS` 的更精确 Cypher，提升复合问句（如「孩子不吃辣，周三要快手晚餐」）的图谱召回精度。
- **同步一致性监控（Chroma ↔ Neo4j）**：`KnowledgeService.consistency_report(family_id)` 比较 Chroma 文档/片段数与 Neo4j `Document`/`KnowledgeEntity` 数，检测两类偏差——仅存在于 Chroma 而图谱未同步的文档、仅存在于 Neo4j 的孤儿节点；任一检索服务不可用时返回对应状态说明而非抛错。新增 `GET /api/v1/admin/rag/sync`（`SyncConsistencyResponse`，`OwnerContext` 鉴权）暴露快照。
- **离线检索质量评测**：新建 `backend/app/services/rag_eval.py`，内置 5 条家庭场景评测集（与引导文档主题对齐），`evaluate_retrieval` 对每条用例运行检索并计算 **Recall@k**（期望命中项在 top_k 中的占比）与 **nDCG@k**（按命中位置折扣增益），不依赖 LLM 在线调用、可重复回归。新增 `GET /api/v1/admin/rag/eval`（`RagEvalResponse`，`top_k` 入参）暴露报告；前端知识库页新增「检索质量与同步监控」面板，展示平均 Recall@k / nDCG@k 指标卡、逐用例结果与同步一致性状态。

**测试覆盖**：新增 `backend/tests/test_graph_rag_quality.py` 共 8 项——rerank 工厂未安装返回 `None`、本地路径加载（断言 `local_files_only=True` 与 `compute_score` 输出）、防隐式下载断言、关闭返回 `None`、实体抽取正则回退、查询改写规则识别、评测 Recall/nDCG 命中与零命中；并修正既有 `test_graph_search_uses_non_conflicting_search_parameter` 以匹配 `search` 新签名（新增 `keywords`/`entity_kinds`/`relations` 参数）。

**验证结果**：后端 `pytest` 新增 8 项 Graph RAG 质量测试**全部通过**，隔离运行 `test_api.py`（33）与 `test_rag.py`+`test_graph_rag_quality.py`（22）均全绿（完整套件一次性运行时存在 6 项既有计划/领域集成测试因依赖实时 LLM 与全局状态顺序间歇失败，与本轮无关，详见第五节）；前端 `npm run build` 通过（2350 模块，KnowledgeView chunk 11.34 kB，类型检查与 SCSS 编译无错）；`python -m py_compile` 全部新模块通过。`FlagEmbedding`（rerank 依赖）与真实 LLM 未部署时，五项功能均优雅降级：rerank 退回首阶段排序、实体抽取退回正则、查询改写退回规则、评测与同步监控返回可解读的说明，检索主链路不受影响。

### 2.21 全量代码复核与剩余缺口校准（2026-08-06 下午）

对 backend/frontend 全量源码做了逐文件复核，校准第四节剩余缺口与真实代码的一致性，结论如下：

**复核确认的真实代码状态**

- 后端：`app/models/identity.py` 集中定义 **22 张业务表**（见第三节修正）；Alembic 共 10 个迁移单链，`20260804_07_celery_hardening` 为 head；`app/ai/workflow.py` 为 **13 节点** LangGraph `StateGraph`；`app/api/router.py`（2034 行）+ `auth_router.py` 覆盖 dashboard/members/calendar/tasks/meals/shopping/inventory/budget/recipes/knowledge/jobs/admin/chat/plans/agents/auth/families 全部业务域；`app/services/` 共 21 个服务模块（含 embeddings/reranker/entity_extractor/query_rewriter/rag_eval/nutrition/unit_conversion/sms 等）。
- 前端：`src/views/` 共 **14 个视图**（Agent/Auth/Budget/Calendar/Chat/Dashboard/Invite/Knowledge/Meals/Members/PlanDetail/Planner/Shopping/Tasks），13 条路由；技术栈为 Vue 3.5 + Vite 7 + Pinia 3 + axios + ECharts 6 + lucide-vue-next + sass（**未使用** PRD 早期规划的 Element Plus / FullCalendar，为自研 SCSS 设计系统，功能已等价覆盖，属于技术选型演进而非缺口）。
- 测试：`tests/` 共 3 个测试文件约 55 用例（test_api 33 + test_rag 14 + test_graph_rag_quality 8）；docker-compose 含 backend/worker/frontend/postgres16/redis7/neo4j5/chroma 共 7 个服务。

**复核纠正的缺口口径**

- **菜谱收藏实为「后端已完成、前端缺入口」**：`is_favorite` 字段（identity.py:417）、`Recipe`/`RecipeUpdate` schema、`PATCH /recipes/{id}` 端点、列表按收藏优先排序、餐食替换服务优先选收藏菜谱均已存在；前端 `types.ts` 已声明 `is_favorite` 但 MealsView/无收藏切换按钮。**本轮已落地**：餐食 Agent 口味学习（`family_taste_profile` 注入 `meal()`，确定性回退按历史反馈排序候选）、`household_recipes.like_count` 字段（迁移 `20260804_08`，替换命中菜谱 +1）、执行反馈闭环（见 8.1）。剩余仅为：前端收藏切换 UI、`meal_feedback` 独立表（当前餐食反馈统一入 `plan_feedback`）、Neo4j `LIKES` 关系。
- **确认不存在的表/功能**（与第四节一致）：`notifications`、`task_series`、`meal_feedback`、`family_reports`、`calendar_integrations` 五张表均无对应模型（`plan_feedback` 已于 2026-08-06 本轮新增）；无 `/notifications`、`/reports` 路由；无 NotificationsView/ReportsView；无 PWA（vite-plugin-pwa/manifest）；无 Playwright/Cypress E2E；无 CI/CD（.github/workflows）；无 OpenTelemetry/Prometheus。**已纠正**：执行反馈闭环本轮已从「仅有 `meals/{id}/replace` 的 feedback 入参」升级为「偏差落库 `plan_feedback` + 回图谱/向量 + 补偿重放 + 前端反馈 UI」的完整回路（见 7.6、8.1）。

**RAG 业务流核实（关键补充）**：Graph RAG「检索质量六项」（BGE-M3 / rerank / 实体关系抽取 / 查询改写 / 同步一致性监控 / 离线评测）确实全部落地（见 2.1、2.20），核心管线完整可用。但存在一处**实时性缺口**：业务 CRUD（创建/修改成员、画像、菜谱、日程、任务、预算）**不会自动推送** Neo4j 图与 Chroma 向量——`sync_family_graph` 仅由 `POST /knowledge/bootstrap` 或手动 `POST /knowledge/jobs/graph-sync` 触发（见 router.py:1389/1403），日历写操作只失效 Redis Dashboard 缓存而非图谱。即图谱/向量与业务主数据同源自 PostgreSQL，但同步是**手动/初始化触发式**而非事件驱动，新业务数据需重新"初始化知识"或手动同步后才进入检索。此外 `POST /knowledge/search` 返回片段+来源，但缺「基于检索生成自然语言答案并标注引用」的端到端知识问答闭环（规划工作流内部已用双路检索，但独立知识问答答案生成未单独封装）；`consistency_report` 仅报告孤儿节点/未同步文档，不自动修复。

**验证结果**：本次为只读复核，未改动代码；上述结论均来自源码直接核对（`identity.py` 全部 `__tablename__`、`router.py` 端点清单、`package.json` 依赖、alembic/versions 目录、frontend/src 目录树）。

## 三、数据库与运行状态

PostgreSQL 实际迁移已升级至 **`20260804_07 (head)`**（`20260804_07_celery_hardening`），Alembic `check` 无模型漂移。

- 当前共有 **22 张 CasaMind 业务表**（users、families、family_memberships、family_member_profiles、calendar_events、event_participants、calendar_event_exceptions、agent_runs、weekly_plans、plan_meal_items、plan_shopping_items、plan_tasks、plan_budgets、task_completions、expense_records、household_recipes、inventory_items、chat_sessions、chat_messages、background_jobs、family_invitations、refresh_sessions）、4 张 LangGraph 官方 checkpoint 表，另有 Alembic 自身的 `alembic_version`，数据库 public schema 共 27 张表。
- `20260804_04` 新增了对话、后台任务、支出、菜谱和任务完成等领域表。
- `20260804_05` 新增 `family_invitations`、`refresh_sessions`、`calendar_event_exceptions`，并扩展 Agent checkpoint、任务周期和菜谱营养字段。
- `checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations` 由 LangGraph 官方 saver 管理，并已从 Alembic autogenerate 中排除。

| 依赖 | 当前定位 |
|---|---|
| PostgreSQL | **认证和业务运行必需**，不可省略 |
| DeepSeek/兼容 LLM | 真实 AI 规划必需；无配置时只能 Demo/Fallback |
| Chroma | 文档向量知识库和完整 RAG 必需 |
| BGE-M3（sentence-transformers） | 可选语义向量增强；未安装或未下载模型时自动回退内置轻量模型 |
| Neo4j | Graph RAG 和真实关系同步必需 |
| Redis | 限流、缓存、取消、锁和 Celery 必需；开发环境部分能力可降级 |
| Celery Worker | 后台文件入库和图谱同步必需 |

当前前端开发服务运行于 `http://127.0.0.1:5173`。后端 `8000` 保持空闲，可由 PyCharm 的 `CasaMind Backend Debug` 启动。

## 四、当前仍未完成的部分

以下是全部已完成项之后的真实剩余缺口，按优先级排列，与第六节推荐后续顺序对应。

1. **领域完整模型**（对应 8.3、8.8）：~~库存~~（已完成：`InventoryItem` 模型 + 增量调整 + 低库存预警）、~~采购订单/历史~~（已完成：支出历史筛选 + 库存双视角）、~~食材单位换算~~（已完成：跨单位求和 + 换算明细）、~~跨计划归档~~（已完成：`archive_plan` + 归档列表，与版本回滚解耦）。剩余：**独立任务系列**（`task_series` 表 + 展开落库 + 单次修改/取消不影响整系列）、**菜谱反馈学习**（菜谱收藏后端已完成——`household_recipes.is_favorite` 字段、schema、`PATCH /recipes/{id}` 端点、列表按收藏排序、餐食选择优先命中收藏均已落地，仅缺前端收藏切换 UI；剩余为 `like_count` + `meal_feedback` 餐食反馈 👍/👎 + 餐食 Agent 口味偏好学习 + Neo4j `LIKES` 关系同步）。
2. **日历生态**（对应 8.5）：完整 RRULE（`rrule` 库）、系列拆分、外部日历双向同步（Google/Outlook OAuth）、时区/夏令时边界、地点和通勤冲突。当前仅支持一次性/每天/每周/每月简单规则与 `calendar_event_exceptions` 单次例外。
3. **认证补全**（当前项目不做）：~~找回密码、邀请链接独立页面、设备会话管理 UI 和指定会话撤销~~（已全部完成）。剩余：邮件验证（本期不做）、邀请链接短信送达（可选增强，当前复制链接方案已闭环）。
4. **Graph RAG 质量**：~~BGE-M3~~（已完成本地接入 + 优雅降级 + 维度隔离）、~~rerank~~（bge-reranker-v2-m3 二阶段精排，见 2.20）、~~复杂实体关系抽取~~（LLM/NER 级 + 正则回退，见 2.20）、~~查询改写~~（复杂 Cypher 改写 + 规则回退，见 2.20）、~~同步一致性监控~~（`/admin/rag/sync`，见 2.20）、~~离线评测~~（Recall@k / nDCG@k，`/admin/rag/eval`，见 2.20）——**检索质量六项全部完成**。剩余（非质量项）：业务 CRUD 事件驱动同步（当前为初始化/手动触发式，见 2.21）、端到端知识问答答案生成（检索已返回片段+来源但未封装答案生成）、监控只读不自愈。
5. **恢复与流式工程化**（对应第六节第三梯队）：~~SSE 断线重连、`Last-Event-ID` 和事件持久化~~（已完成：事件 ID + Redis 持久化 + 重放端点 + 前端断线自动补齐）。剩余：人工编辑/分支恢复（从任意 checkpoint 创建分支继续执行）、checkpoint 清理策略（定期清理过期 checkpoint 防 PostgreSQL 膨胀）。
6. **报告与通知**（对应 8.2、8.4）：家庭月报（任务完成率/预算执行/营养达成/LLM 洞察）、通知中心（`notifications` 表 + Bell 图标未读计数 + 下拉面板）、逾期提醒、预算告警推送和任务完成率报告。当前仪表盘仅有今日过期安排数与预算使用率等基础 notice。
7. **集成测试与生产部署**（对应第六节第三梯队）：真实 PostgreSQL/Chroma/Neo4j/Redis/Celery 自动化集成测试、浏览器 E2E（Playwright/Cypress）、CI/CD、HTTPS、备份恢复和零停机迁移。当前仅有 pytest 单元/集成测试（47 项），无 E2E 与 CI/CD。
8. **安全与可观测性**（对应第六节第三梯队）：Prompt Injection 防护、上传文件扫描、敏感信息脱敏（当前仅前端脱敏，API 响应仍含技术词汇）、审计日志、结构化日志（当前 loguru 仅控制台输出）、Tracing（OpenTelemetry）、Metrics（Prometheus）、告警和 Token/模型成本追踪。

## 五、验证结果

- PostgreSQL Alembic：**`20260804_07 (head)`**（新增 `20260804_07_celery_hardening` 为 `background_jobs` 表增加 `idempotency_key`/`priority` 字段及 `ix_background_jobs_idem` 索引）。
- 数据库模型漂移：**无**。
- 后端 `pytest`：**本轮新增 8 项 Graph RAG 质量测试全部通过**；隔离运行 `tests/test_api.py`（33 通过）与 `tests/test_rag.py` + `tests/test_graph_rag_quality.py`（22 通过）均全绿。完整套件一次性运行时存在 6 项**既有**计划/领域集成测试（plan 版本、跨家庭隔离、任务分配等）因依赖实时 LLM 与数据库/全局状态顺序而间歇失败，与本轮 Graph RAG 改动无关（已通过隔离运行验证：移除新增测试文件后原 `test_rag.py` + `test_api.py` 同样复现该 6 项失败，确认属既有测试隔离问题）。新增测试覆盖：rerank 工厂未安装返回 None / 本地路径加载（断言 `local_files_only=True`）/ 防隐式下载 / 关闭返回 None、实体抽取正则回退、查询改写规则识别、评测 Recall@k/nDCG@k 命中与零命中；并修正既有 `test_graph_search_uses_non_conflicting_search_parameter` 以匹配 `search` 新签名。
- 后端 `ruff check app tests alembic`：**通过**。
- 后端 `mypy app tests`：**通过，46 个源文件**。（注：`identity.py:256` 存在 1 个 SQLAlchemy `Row[tuple[...]]` 类型预存告警，为预存问题；2.5 节新增模块 `prompts.py`/`evaluation.py`/`nutrition.py` 及 `InventoryItem` 模型、2.7 节新增的 `runtime.py` 幂等原语、`worker.py` `DeadLetterTask`/`cancel_running_task`、`router.py` 监控/取消/清理/死信端点与 `domain.py` 新增 schema 均无类型错误。）
- 前端 `npm run build`：**通过，所有模块完成构建**。
- **2.19 节前端集成验证（2026-08-06）**：`vue-tsc -b && vite build` 成功（14.77s，2350 模块），`AuthView.vue` 移除 `LoginMethod` 类型、`loginMethod` ref 和互斥切换控件，改为密码字段与短信验证码字段上下分区同时可见，模板中 `v-if="mode !== 'login'"` 块内移除冗余类型窄化检查，`App.vue` RouterView 与 AppShell 均绑定 `:key="route.fullPath"`，全部通过类型检查。AuthView chunk 从 10.71 kB 增至 10.98 kB（+270 B）。
- **2.18 节前端集成验证（2026-08-06）**：`vue-tsc -b && vite build` 成功（7.26s，2350 模块），`router.ts` 的 `lazy()` 包装器与拓宽后的 `onError` 正则通过类型检查；`main.scss` 排版尺度变量与「Typography readability pass」覆盖层 SCSS 编译无错，CSS 产物 73.28 kB（gzip 14.22 kB）。dev 环境路由切换 chunk 失效可自愈重试或兜底重载，不再需要手动刷新；全站字体达 WCAG AA 可读性下限。
- **2.5 节前端集成验证**：`vue-tsc -b && vite build` 成功（6.56s），MealsView（营养面板）、ShoppingView（库存抽屉）、AgentView（评测/提示词面板）、PlanDetailView（归档抽屉）四个视图均通过类型检查并完成打包；同时修复 `AIServiceStatus` 类型缺失 `embedding` 字段的预存不同步问题。
- **2.7 节前端集成验证**：`vue-tsc -b && vite build` 成功（6.43s，2350 模块），AgentView 新增「Celery 队列监控」面板（broker 状态 + 4 队列深度 + 状态分布 + 最近任务含取消 + 手动清理）与「死信任务」面板（死信列表联动刷新）均通过类型检查；`types.ts` 新增 `QueueStats`/`CeleryStatsResponse`/`DeadLetterItem`，`api.ts` 新增 `celeryStats`/`cancelJob`/`deadLetterJobs`/`cleanupJobs` 四个方法。
- 阿里云号码认证：**真实短信发送验收通过**（`code: OK / success: True`，2026-08-05 15:41）。
- 前端开发服务：**HTTP 200，端口 5173**。
- Celery：**已连接 Redis，4 条队列（default/knowledge/graph/maintenance）+ 3 类业务任务 + 清理定时任务注册成功，`DeadLetterTask` 基类已接入**。
- **2.1 节 BGE-M3 真机验证（2026-08-05）**：模型已下载至 `D:\software\tools\modes\bge-m3`；后端 venv 已装 `torch 2.13.0+cpu` + `sentence-transformers 5.6.1`；`.env` 已配 `EMBEDDING_PROVIDER=bge-m3`；嵌入后端解析为 `本地语义模型 BGE-M3`（`is_bge_m3=True`），`encode` 产出 1024 维向量；真 Chroma 容器已创建 `casamind_knowledge-bge-m3` 集合（`count=0`，待"初始化知识"灌入），心跳正常；后端服务 `--reload` 模式运行于 `127.0.0.1:8000`。

自动化测试现覆盖原有认证、多家庭隔离、成员画像、日历 CRUD/周期/冲突、Calendar Agent、计划确认和版本，以及本轮新增的 Refresh 轮换撤销、邀请和角色、周期 occurrence 例外、checkpoint、计划派生/对比、会话管理、Neo4j 查询参数、后台任务入队和邀请接受会话签发。

## 六、推荐后续顺序

以下按「投入产出比 + 演示价值 + 架构契合度」排列，均沿用现有 FastAPI + SQLAlchemy + LangGraph + Celery + Vue 3 技术栈与代码风格。

```text
第一梯队（高价值、可快速落地）
├─ 8.1 执行反馈闭环 ✅（本轮已落地）── 补齐「采集→规划→执行→反馈」回路的最后一环：plan_feedback 偏差表 + 回图谱/向量 + 补偿重放 + 前端 TasksView/ShoppingView/MealsView 反馈 UI
├─ 8.2 通知中心与主动提醒 ── 补齐第四节缺口 6（报告与通知），逾期任务/预算告警/日程提醒/低库存预警主动推送，前端 Bell 图标未读计数徽章
└─ 8.3 菜谱收藏与反馈学习 ✅（口味学习本轮已落地）── 餐食 Agent 学习家庭口味偏好（family_taste_profile），优先推荐高评分菜谱；前端收藏切换 UI / meal_feedback 独立表 / Neo4j LIKES 仍待做

第二梯队（中价值、需一定工程量）
├─ 8.4 家庭月报与洞察 ── Celery Beat 每月 1 号生成月度报告（任务完成率/预算执行/营养达成/LLM 洞察），前端月报视图 + ECharts + PDF 导出
├─ 8.5 外部日历双向同步 ── 补齐第四节缺口 2（日历生态），Google/Outlook OAuth + 完整 RRULE + 系列拆分，与现有 calendar_event_exceptions 融合
├─ 8.6 PWA 化与离线能力 ── vite-plugin-pwa，购物清单/任务看板离线浏览与本地修改，上线后同步

第三梯队（低优先级、工程化增强）
├─ 8.8 任务独立系列与 recurring 引擎增强 ── 补齐第四节缺口 1（独立任务系列），task_series 表 + 展开落库 + 单次修改/取消不影响整系列
├─ 第四节缺口 5（恢复与流式工程化）── 人工编辑/分支恢复、checkpoint 清理策略（SSE 断线重连已完成）
├─ 第四节缺口 7（集成测试与生产部署）── 真实 PostgreSQL/Chroma/Neo4j/Redis/Celery 自动化集成测试、浏览器 E2E、CI/CD、HTTPS、备份恢复
└─ 第四节缺口 8（安全与可观测性）── Prompt Injection 防护、上传文件扫描、敏感信息脱敏、审计日志、结构化日志、Tracing、Metrics、告警、Token/模型成本
```

> 注：Graph RAG 检索质量（rerank / 实体关系抽取 / 查询改写 / 同步监控 / 离线评测）五项已于 2.20 节全部完成；SSE 断线重连与事件持久化已于 2.6 节完成；领域业务深化（采购历史/任务展开/单位换算/营养目标/库存/跨计划归档）已于 2.5 节完成；Redis/Celery 深化（幂等键/队列隔离/取消/清理/死信/监控）已于 2.7 节完成。邮件验证本期明确不做，邀请链接短信送达为可选增强。

## 七、详细业务流程

CasaMind 的核心业务是一个「**家庭数据采集 → AI 周计划生成 → 计划持久化与版本管理 → 执行跟踪 → 反馈监控**」的闭环。下图与各层说明依据当前源码（`app/ai/workflow.py`、`app/repositories/planning.py`、`app/api/router.py`、`app/worker.py`）梳理。

### 7.1 闭环总览

```text
┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐   ┌──────────────┐   ┌──────────────┐
│ 1. 数据采集  │ -> │ 2. AI 周计划生成  │ -> │ 3. 计划持久化    │ -> │ 4. 执行跟踪   │ -> │ 5. 反馈监控   │
│ 家庭/日程/   │   │ LangGraph 13 节点 │   │ weekly_plans    │   │ 任务看板/    │   │ 仪表盘/营养  │
│ 菜谱/任务/   │   │ Graph RAG + 多智能体│   │ 版本链/激活/归档 │   │ 购物核销/    │   │ 达成率/Celery│
│ 预算/知识库  │   │ SSE 流式 + Checkpoint│ │                 │   │ 预算追踪    │   │ 监控/死信     │
└─────────────┘   └──────────────────┘   └─────────────────┘   └──────────────┘   └──────┬───────┘
      ^                                                                                     │
      └────────────────────────── 反馈数据回流（部分实现，见 7.6） ──────────────────────────────┘
```

### 7.2 数据采集层（输入）

家庭数据通过 REST API 写入 PostgreSQL，全部带 `family_id` 多租户隔离；部分实体同步至 Neo4j 图与 Chroma 向量库供 RAG 检索。

| 数据域 | 写入端点（示例） | 持久化表 | 图/向量同步 |
|---|---|---|---|
| 家庭成员画像 | `POST /api/v1/members` | `family_members` | Neo4j `Member` |
| 家庭日程 | `POST /api/v1/calendar` | `calendar_events` + `calendar_event_exceptions` | Neo4j `Event` |
| 菜谱 | `POST /api/v1/recipes` | `recipes` | Neo4j `Recipe`/`Ingredient` |
| 任务 | `POST /api/v1/tasks` | `tasks` | Neo4j `Task` |
| 预算/支出 | `POST /api/v1/budget/expenses` | `expenses` | Neo4j `Budget` |
| 库存 | `POST /api/v1/inventory/adjust` | `inventory_items` | — |
| 知识文档 | `POST /api/v1/knowledge/jobs/{text,file}` | `background_jobs` → 切片入 Chroma | Celery 异步入库 |

成员画像（过敏/忌口/偏好/空闲时间）、日程约束会作为 `WorkflowState.members`/`events` 注入每次 AI 规划。

### 7.3 AI 规划层（核心，13 节点 LangGraph）

入口 `POST /api/v1/planner/stream`（SSE）启动 `WorkflowRunner.run()`，通过 `self._graph.astream()` 流式执行官方 PostgreSQL Checkpointer 持久化的状态机。13 节点按执行顺序：

```text
intent            ── 意图解析：理解用户自然语言请求，判定规划类型与约束
  │
  ├─> graph_retriever   ── 图检索：Neo4j 查询家庭关系/菜谱/任务/预算实体
  ├─> vector_retriever  ── 向量检索：Chroma（BGE-M3 1024 维 / 内置 384 维）召回知识片段
  │
  v
coordinator       ── 协调器：融合图/向量结果 + 成员/日程上下文，分派给领域 Agent
  │
  ├─> calendar_agent    ── 日历 Agent：识别时间冲突与可用窗口
  ├─> meal_agent        ── 餐食 Agent：兼顾过敏/忌口/口味/时间生成一周晚餐
  ├─> shopping_agent    ── 购物 Agent：从菜单合并食材 + 单位换算
  ├─> task_agent        ── 任务 Agent：按成员空闲时间与历史负担公平分配
  ├─> budget_agent      ── 预算 Agent：估算开支并卡限额
  │
  v
domain_coordinator ── 领域协调：汇总四领域结果，交叉校验（如购物总价 vs 预算）
  │
  v
planner            ── 规划器：组装完整周计划草稿
  │
  v
verifier           ── 校验器：检查完整性/冲突/预算超支，产出 validation_warnings
  │
  v
final_planner      ── 终稿：输出最终计划 JSON + 摘要 + trace，SSE 推送 complete
```

- 流式事件：`message`/`step`/`token`/`complete`/`cancelled`/`error`，每个事件带 `{session_id}:{seq}` ID 并 Redis 持久化，支持断线重连补齐（`GET /chat/sessions/{id}/events?after=`）。
- 恢复能力：每节点完成写 `agent_runs.checkpoint`；PostgreSQL 环境失败 Run 沿用原 `run_id` 从 pending 节点续跑，不重跑已完成节点。
- 评测：`evaluate_plan` 对餐食/购物/任务/预算四维度加权（35/25/25/15）输出 0-100 分及扣分原因，`GET /api/v1/agents/evaluate` 暴露。

### 7.4 计划持久化与版本管理

用户在 PlannerView 确认计划 → `POST /api/v1/plans/confirm` → `PlanningRepository.create_confirmed_plan` 事务写入 `weekly_plans` + `plan_meal_items`/`plan_shopping_items`/`plan_tasks`/`plan_budgets`（含 `family_id` 隔离 + `selectinload` 预加载）。版本管理支持：版本列表、激活、回滚、字段级差异、从任意版本派生新活动版本（复制明细并保持父子关系）、独立归档（`archive_plan` 与版本链解耦）。计划确认后主动失效 Redis Dashboard 缓存。

### 7.5 执行跟踪层

| 子域 | 前端视图 | 后端能力 |
|---|---|---|
| 任务 | TasksView 看板（待办/进行中/已完成） | 状态推进、完成记录、实际时长、周期任务展开预览 |
| 购物 | ShoppingView | 勾选核销、合并食材、单位换算明细、库存调整、采购历史 |
| 餐食 | MealsView | 一周晚餐、营养目标达成率求解 |
| 预算 | BudgetView | 分类支出、ECharts 趋势、AI 节省建议、预算预警 |

所有写操作遵循「乐观更新 + 失败回滚」前端策略 + Toast 反馈。

### 7.6 反馈与监控层

- 仪表盘：`GET /dashboard` 按真实用户/家庭计算 greeting、date_label、notices（今日过期安排/预算使用率）、今晚餐、任务进度、周进度，30s Redis 缓存（键含 `family_id` + `user_id`）。
- Celery 监控：`GET /api/v1/admin/celery/stats` 返回 broker 连通性、4 队列深度、任务状态计数、最近任务、死信数量；死信任务面板 + 手动清理。
- **执行反馈闭环（本轮已落地）**：任务完成（`POST /tasks/{id}/complete`，含评分/备注）、餐食替换（`POST /meals/{id}/replace`，含评分/标签）、购物核销（未购→已购，含实付金额/备注）、支出录入（`POST /budget/expenses`）四处执行动作均自动写 `plan_feedback` 偏差记录，并回流至 Neo4j（`FeedbackSignal` 挂在 `Family` 下，正向/负向另连 `Preference`）与 Chroma（固定 `document_id` 滚动文档，上限 30 条）。`FeedbackRepository.taste_profile()` 聚合餐食反馈 + 菜谱 `like_count` 生成口味画像，注入 `meal()` 系统提示与确定性回退排序；`GET /meals/taste-profile`、`GET /feedback`、`POST /feedback/resync` 提供画像查询、偏差总览与补偿重放；前端 TasksView / ShoppingView / MealsView 均已接入反馈采集与回流状态展示。详见 8.1。

## 八、业务功能扩展方案

以下方案按「投入产出比 + 与现有架构契合度」排序，均沿用现有 FastAPI + SQLAlchemy + LangGraph + Celery + Vue 3 技术栈与代码风格，给出落地路径。

### 8.1 执行反馈闭环（优先级：高，闭环价值最大）—— ✅ 本轮已落地

**痛点**：当前「采集 → 规划 → 执行」单向流动，执行偏差（如实际支出超预算、餐食没做、任务延期）未回流影响下次规划。

**实现（2026-08-06）**：
- 后端新增 `plan_feedback` 偏差表（迁移 `20260804_08`）+ `household_recipes.like_count`；在任务完成、餐食替换、购物核销、支出录入四处执行动作经 `FeedbackLoopService.capture()` 单一入口落库 + 回 Neo4j + 回 Chroma（滚动文档），外部依赖不可用仅降级同步标记。
- `FeedbackRepository.taste_profile()` 聚合餐食反馈与菜谱 `like_count` 生成口味画像；`WorkflowState.taste_profile` 经 `app/services/planning.py` 加载并注入 `meal()`（LLM 路径写 prompt + 确定性回退路径直接排序候选），`meal` prompt 升级至 `1.2.0`。
- `GET /meals/taste-profile`、`GET /feedback`（偏差总览 + 情感分布 + 待补偿同步数）、`POST /feedback/resync`（补偿重放）三端点；前端 TasksView（完成评分弹窗）、ShoppingView（核销实付/备注弹窗）、MealsView（口味画像面板 + 替换评分/标签 + 回流状态）全部接入。
- 反馈子图刻意挂在 `Family` 下规避 `sync_family_graph` 全量重建 `DETACH DELETE`，无需新增 Celery 任务。

**后续可选增强**：`domain_agents.py` 各 Agent（calendar/shopping/task/budget）输入新增 `recent_feedback` 模式注入（如「连续 3 周购物超预算 15%」）让 LLM 主动校准；`GET /api/v1/plans/{id}/feedback` 偏差摘要；前端 PlanDetailView「执行复盘」面板；复用 `evaluate_plan` 把历史偏差率纳入综合评分。

### 8.2 通知中心与主动提醒（优先级：高，补齐报告/通知缺口）

**痛点**：第四节缺口 6「报告与通知」未实现，逾期任务/预算告警/日程提醒无主动推送。

**方案**：
- 新增 `notifications` 表（`family_id`/`user_id`/`type`/`title`/`body`/`link`/`read_at`），`GET /api/v1/notifications` 列表 + `POST /.../read` 标记已读。
- Celery Beat 新增定时扫描任务（复用 `maintenance` 队列）：每日早 8 点扫描当日日程、逾期任务、预算超阈值、低库存，批量生成通知。
- 前端顶栏 Bell 图标接入未读计数徽章 + 通知下拉面板；可选接入现有阿里云短信通道推送关键提醒（如「今晚有家庭安排」）。
- 复用现有 Toast 系统做站内即时反馈，与定时通知分层。

### 8.3 菜谱反馈学习（优先级：中，补齐领域缺口；收藏后端已完成）—— ✅ 口味学习本轮已落地

**痛点**：餐食 Agent 无法学习家庭口味偏好。菜谱收藏后端已闭环（`is_favorite` 字段 + `PATCH /recipes/{id}` + 收藏优先排序/选菜，见 2.21），但前端无切换入口，且缺少反馈数据采集与学习回路。

**已实现（2026-08-06）**：`family_taste_profile` 口味画像（聚合餐食反馈 + 菜谱 `like_count`）已注入 `meal()` 与确定性回退排序；`household_recipes.like_count` 字段已加；MealsView 反馈替换支持评分/标签，并展示「系统学到的口味画像」面板与每次替换的回流状态。详见 8.1。

**后续可选增强**：前端 MealsView/菜谱列表新增收藏切换按钮（调用既有 `PATCH /recipes/{id}`，乐观更新 + Toast）；`household_recipes` 增 `last_served_at` 字段；独立 `meal_feedback` 表（当前餐食反馈统一入 `plan_feedback`）；MealsView 每餐卡片 👍/👎 快捷反馈；偏好数据同步至 Neo4j `LIKES` 关系，Graph RAG 检索时按口味权重排序。

### 8.4 家庭月报与洞察（优先级：中，提升留存与产品深度）

**痛点**：缺乏周期性总结，用户难感知长期价值。

**方案**：
- Celery Beat 每月 1 号生成 `family_reports`（任务完成率、预算执行、营养达成、最常做菜谱、节省金额），用 LLM 生成自然语言洞察。
- 新增 `GET /api/v1/reports/monthly?month=YYYY-MM`；前端新增「家庭月报」视图（ECharts 趋势 + LLM 洞察卡片 + 导出 PDF，复用现有 `@media print` 样式）。
- 可复用 `evaluate_plan` 的多维度评分做月度健康分。

### 8.5 外部日历双向同步（优先级：中，补齐日历生态）

**痛点**：第四节缺口 2「外部日历同步」未实现，家庭日程需手动维护。

**方案**：
- 新增 `calendar_integrations` 表存 OAuth token（Google/Outlook），Celery `graph` 队列新增双向同步任务。
- 完整 RRULE（`rrule` 库）+ 系列拆分，与现有 `calendar_event_exceptions` 例外机制融合。
- 前端 CalendarView 新增「绑定外部日历」入口。

### 8.6 PWA 化与离线能力（优先级：中，移动端体验）

**痛点**：当前为纯 Web，移动端无离线能力、无安装入口。

**方案**：
- Vite 加 `vite-plugin-pwa`，生成 manifest + service worker，缓存静态资源与近期 API 响应。
- 购物清单/任务看板支持离线浏览与本地修改，上线后同步（复用现有乐观更新 + 回滚策略）。
- 移动端底部 Tab 已就绪，PWA 化后可「添加到主屏」近似原生体验。

### 8.7 Graph RAG 检索质量评测与 rerank（优先级：中低，技术深度）—— **本轮已实现，详见 2.20**

**痛点**：第四节缺口 4「检索评测/rerank」未实现，BGE-M3 已接入但无量化评估。

**方案（已全部落地）**：
- ✅ 离线评测集（家庭场景 query + 期望命中文档/实体类型），计算 Recall@k / nDCG；`GET /api/v1/admin/rag/eval` 暴露报告。
- ✅ 接入 `bge-reranker-v2-m3` 做二阶段精排，与 BGE-M3 召回组合（reranker.py + `KnowledgeService.retrieve_vector` 两阶段）。
- ✅ 前端 KnowledgeView 新增「检索质量与同步监控」面板，展示指标卡、逐用例结果与同步一致性。

### 8.8 任务独立系列与 recurring 引擎增强（优先级：低，补齐领域生命周期）

**痛点**：第四节缺口 1「独立任务系列」未实现，周期任务展开仅为只读预览。

**方案**：
- 新增 `task_series` 表，周期任务展开后落库为独立任务实例（共享 `series_id`），支持单次修改/取消不影响整系列（复用日历例外机制思路）。
- `expand_recurring_tasks` 从只读预览升级为实际生成，与通知中心联动自动提醒。

# SoloChef 个人项目分析报告

> **文档版本**：v9.0
> **编制日期**：2026-08-09
> **项目定位**：SoloChef — AI 独居膳食与采买规划师
> **评估方法**：逐文件通读后端源码（`backend/app`，54 个 Python 源文件）、前端工程（`frontend/src`，23 个源文件）、`docker-compose.yml`、Dockerfile、Alembic 迁移、PRD 与 UI 设计文档；所有结论均标注代码出处（文件 + 类/函数名），可直接复核
> **核心结论**：后端工程化基础与 AI/RAG 能力较完整（**pytest 54 passed / ruff 全绿 / mypy 54 源文件无问题**），**前端去家庭化已完成**（4 个家庭页面已删除、2 个新页面已创建、路由/类型/品牌全量更新）。**Phase 1 营养闭环、Phase 2 技术债务清理、Phase 3 深化均已全部完成**：营养目标 API 闭环已打通（`GET/PUT /profile` + `POST /profile/nutrition-goal`，Mifflin-St Jeor TDEE 公式已实现，Verifier 第 6 项营养达成率校验 [90%, 110%]）；工作流从 13 节点精简为 11 节点（移除 `task_agent` / `calendar_agent`）、LangGraph checkpoint 已迁移至 `InMemorySaver`、种子知识库与 Demo 数据已全量换血为独居膳食向；**Phase 3 深化已落地**——6 张遗留表清理（20→14 表，alembic 0002 迁移）、食材营养库扩充至 105 种（外置 JSON + 校准标注，对齐《中国食物成分表第6版》）、git 仓库初始化（`main` 分支）+ GitHub Actions 四道门禁 CI（ruff / mypy / pytest / alembic 迁移）。**后端开发流程三个 Phase 全部完成**，项目进入可交付状态。

---

## 目录

1. [项目概述](#一项目概述)
2. [项目业务流程图](#二项目业务流程图)
3. [功能状态说明](#三功能状态说明)
4. [工作流设计（开发/测试/部署/版本控制/代码审查/CICD）](#四工作流设计)
5. [Agent 设计流程](#五agent-设计流程)
6. [RAG（检索增强生成）设计流程](#六rag检索增强生成设计流程)
7. [下一步计划](#七下一步计划)
8. [附录](#八附录)

---

## 一、项目概述

### 1.1 定位与边界

SoloChef 由 CasaMind/HomePilot「家庭综合事务规划师」收敛而来（2026-08-07 完成定位切换），面向**独居自炊人群**，细分为增肌 / 减脂 / 健康维护三类目标用户。一句话定位：

> 按营养目标（TDEE + 宏量营养素）约束式生成每日三餐与精确购物清单，并通过执行反馈持续学习个人口味、预算与达标情况的 AI 应用。

**明确不做**（MVP 边界，写入 PRD 第 1/5 节）：家庭协同、任务看板、完整日历、通用知识库问答、外部同步、商超实时比价、真实下单、剩余食材利用。

### 1.2 技术栈总览

| 层级 | 选型 | 代码出处 |
|---|---|---|
| 前端 | Vue 3.5 + TypeScript + Vite 7 + Pinia 3 + vue-router 4 + ECharts 6 + axios | `frontend/package.json` |
| 后端框架 | FastAPI 0.115+ / Pydantic v2 / SQLAlchemy 2.x（async）/ Loguru | `backend/pyproject.toml` |
| AI 编排 | LangGraph（StateGraph）+ LangChain（langchain-openai ChatOpenAI） | `backend/app/ai/workflow.py` |
| LLM | DeepSeek（`deepseek-chat`，OpenAI 兼容协议，可换 base_url），demo 确定性生成器兜底 | `backend/app/ai/llm.py` |
| 主业务库 | MySQL 8（aiomysql 异步驱动）；测试用 SQLite（aiosqlite）；Alembic 迁移 | `backend/app/core/config.py`、`backend/alembic/versions/0001_initial_solochef.py` |
| 图数据库 | Neo4j 5 Community（官方异步驱动） | `backend/app/services/graph_store.py` |
| 向量库 | Chroma 1.5.9（HttpClient，cosine HNSW） | `backend/app/services/vector_store.py` |
| 嵌入/精排 | BGE-M3（sentence-transformers，1024 维，本地加载）↔ 内置 ONNX MiniLM-L6-v2（384 维）双轨；bge-reranker-v2-m3（FlagEmbedding）二阶段精排 | `backend/app/services/embeddings.py`、`reranker.py` |
| 缓存/队列 | Redis 7（缓存、限流、SSE 事件日志、幂等键、分布式锁）+ Celery 5（4 队列 + beat 定时清理 + 死信） | `backend/app/services/runtime.py`、`backend/app/worker.py` |
| 认证 | JWT（access 30min / refresh 7d，token_version 失效控制）+ 阿里云短信验证码登录 | `backend/app/api/auth_router.py`、`backend/app/core/security.py` |
| 部署 | docker-compose 7 服务（backend / worker / frontend / mysql / redis / neo4j / chroma），前端 nginx 反代 | `docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile` |

### 1.3 验证基线（2026-08-09 复核）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 后端单元/接口测试 | `uv run pytest --cache-clear`（需 `DATABASE_URL=sqlite+aiosqlite:///:memory:`） | **54 passed**（`test_api.py` + `test_rag.py` + `test_graph_rag_quality.py`，遗留表清理后移除 calendar/tasks/inventory 相关用例） |
| 代码风格 | `uv run ruff check app tests`（E/F/I/UP/B/SIM，行宽 100） | All checks passed |
| 静态类型 | `uv run mypy app`（严格模式 `disallow_untyped_defs`） | Success，54 source files |
| 前端构建 | `npm run build`（含 `vue-tsc -b` 类型检查） | 通过，`frontend/dist/` 产物存在 |
| 前端去家庭化 | 路由/类型/视图文件审计 | ✅ 已完成（4 页面删除、2 页面新建、23 源文件全量更新） |
| 持续集成 | `.github/workflows/ci.yml` | ✅ 四道门禁就绪（ruff / mypy / pytest / alembic 迁移），PR 与 push 到 main 触发 |
| 版本控制 | `git log` | ✅ `main` 分支，Phase 3 提交 `738e886` 已落地 |

---

## 二、项目业务流程图

### 2.1 图 1：用户操作流程图（端到端主旅程）

描述真实用户在系统中的完整操作路径。标注 **[未实现]** 的节点为当前尚未落地的断点（详见第三章）。

```mermaid
flowchart TD
    A([开始]) --> B[注册 / 登录]
    B --> C[录入身体数据与偏好]
    C --> D["营养目标计算 BMR/TDEE  [未实现]"]
    D --> E[(保存画像与营养目标)]
    E --> F[发起规划请求]
    F --> G[LangGraph 工作流生成计划]
    G --> H["预览: 三餐 + 购物 + 预算 + 校验"]
    H --> I{用户确认?}
    I -- 调整 --> F
    I -- 保存 --> J[(计划落库)]
    J --> K[执行: 做饭 / 采购 / 核销]
    K --> L{换菜?}
    L -- 是 --> M[换菜 + 反馈回流]
    L -- 否 --> N[采购核销 + 反馈回流]
    M --> O[(反馈偏差表)]
    N --> O
    O --> P[反馈回流: 图谱 + 向量 + 口味画像]
    P --> Q[(Neo4j + Chroma)]
    Q -. 下轮检索注入 .-> G
    P --> R([下次规划自动学习])
```

**关键断点说明**：`C → D → E` 一段目前只有数据表（`UserProfile` / `NutritionGoal`，`models/identity.py`）而无 API 与计算实现——全代码库检索不到 Mifflin-St Jeor 的任何实现（仅模型 docstring 提及），也没有任何端点写入这两张表。营养目标当前只能依赖建表默认值（2000 kcal / 120g / 220g / 60g）参与 `/meals/nutrition` 达成报告。

### 2.2 图 2：数据流转流程图

以「生成一周计划」这条最核心的请求为例，展示数据在各基础设施组件间的流转：

```mermaid
flowchart TD
    UI[前端 Vue3] -->|REST / SSE| API[FastAPI 鉴权]
    API --> SVC[服务层编排]
    SVC --> DB[(MySQL 14 张表)]
    SVC --> WF[LangGraph 工作流]
    WF --> KS[知识检索服务]
    KS --> N4[(Neo4j 图谱)]
    KS --> CH[(Chroma 向量库)]
    WF --> LLM[DeepSeek LLM]
    SVC --> RD[(Redis 缓存/限流/事件)]
    SVC --> CE[Celery 异步任务]
    CE --> CH
    CE --> N4
    SVC --> FB[反馈回流服务]
    FB --> DB
    FB --> N4
    FB --> CH
```

**数据一致性设计要点**（代码实证）：
- 每步 Agent 轨迹实时写入 `agent_runs.checkpoint`（`planning.py::persist_step`），失败可定位到 `failed_step`；
- 反馈回流失败不阻断主链路，仅置 `synced_to_graph/vector=false`，由 `/feedback/resync` 补偿重放（`feedback_loop.py::replay`）；
- 知识入库走 Celery 异步 + 幂等键（Redis `idem:{key}`，24h），重复提交直接返回既有 job 结果；
- 所有跨组件写均为「先 MySQL 落库，后异步回流」的单向数据流，不存在双写不一致窗口。

### 2.3 图 3：核心业务逻辑关系图

展示后端代码层面的模块组织与「计划 → 执行 → 反馈 → 记忆 → 再计划」闭环的逻辑依赖：

```mermaid
flowchart TD
    P[规划生成] --> C[计划确认]
    C --> E[执行与核销]
    E --> F[反馈捕获]
    F --> M[记忆沉淀]
    M --> T[口味画像聚合]
    T -. 注入下轮 .-> P
    N[营养达成报告] -.-> C
    K[知识库检索] -.-> P
    B[预算分析] -.-> F
    TR[Agent 轨迹追踪] -.-> P
    EV[RAG 检索评测] -.-> K
```

闭环中 `T → P` 的虚线是本项目的差异化关键：口味画像不是一次性入参，而是跨会话累积的长期记忆。

### 2.4 图 4：执行反馈闭环时序图

以「用户对周三晚餐不满意并换菜」为例，展示闭环的精确调用序列（全部可在代码中复核）：

```mermaid
sequenceDiagram
    participant U as 用户
    participant API as FastAPI
    participant DOM as DomainService
    participant FB as FeedbackLoop
    participant DB as MySQL
    participant N4 as Neo4j
    participant CH as Chroma

    U->>API: POST /meals/{id}/replace (feedback=太辣, rating=2)
    API->>DOM: replace_meal(user_id, meal_id, feedback)
    DOM->>DB: 读取菜谱库 + taste_profile
    alt 有 LLM
        DOM->>DOM: ChatOpenAI 生成替换候选
    else 无 LLM
        DOM->>DOM: 确定性回退: 按口味排序菜谱
    end
    DOM->>DB: 替换餐食; 命中菜谱 like_count+1
    DOM->>FB: capture(FeedbackSignal)
    FB->>DB: 写入 plan_feedback
    FB->>N4: 反馈子图 HAS_FEEDBACK/SIGNALS
    FB->>CH: 滚动反馈文档 窗口30
    FB->>DB: mark_synced(graph, vector)
    FB-->>API: FeedbackSyncResult
    API-->>U: 新餐食 + 口味画像
```

---

## 三、功能状态说明

### 3.1 总体完成度

| 范围 | 完成度 | 关键依据 |
|---|---:|---|
| 后端基础设施（认证/模型/路由/异步/观测） | **95%** | 87 个 REST 端点、14 张表（遗留 6 表已清理）、Celery/Redis 全链路、JWT 双 token |
| AI/Agent 编排 | **80%** | 11 节点工作流可运行、结构化领域智能体双路径、Verifier 确定性校验含营养达成率；checkpoint 用 InMemorySaver 跨方言可用 |
| RAG 检索增强 | **85%** | 双路召回 + 二阶段精排 + 查询改写 + 实体抽取 + 离线评测齐备；种子文档已独居膳食向 |
| 营养目标闭环 | **90%** | 表结构 + 营养报告服务 + 画像/目标 API + TDEE 计算 + Verifier 营养校验 + 食材营养库 105 种（校准标注） |
| 前端产品体验 | **65%** | 11 个视图已去家庭化（4 删 2 新）、路由/类型/品牌全量更新；但营养目标页缺后端 API 对接、部分视图仍引用遗留类型 |
| 工程化（测试/lint/类型/容器/CI） | **90%** | 三重质量门禁全绿、compose 一键起栈；✅ git 仓库 + GitHub Actions CI 四道门禁已就绪 |

### 3.2 已完成功能模块详表

#### 3.2.1 身份与数据层（实现度 100%）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| 注册/登录/刷新/登出 | 密码 + 短信双通道；短信登录自动开户（随机密码）；改密/重置密码后全端会话吊销 | `auth_router.py`；`services/auth.py` + `services/sms.py`（阿里云 dysmsapi） |
| JWT 会话体系 | access 30 分钟 + refresh 7 天；`token_version` 支持全端失效；设备会话列表与单会话吊销（`refresh_sessions` 表） | `core/security.py`；`api/dependencies.py::get_current_context` |
| 单用户数据隔离 | 全部业务表以 `user_id` 外键隔离；`AuthContext` 请求级注入；`OwnerContext` 语义化写操作守卫 | `api/dependencies.py`；14 张表模型 `models/identity.py` |
| MySQL 方言适配 | 启动幂等 `create_all` 兜底 + Alembic 初始迁移；JSON 列 `default=list` 跨方言兼容；去 Postgres 专用语法 | `main.py::_create_tables`；`alembic/versions/0001_initial_solochef.py` |

#### 3.2.2 计划与执行域（实现度 90%，去家庭化 + 遗留表清理完成）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| 周计划生命周期 | 生成 → 确认落库 → 派生新版本 → 版本 diff → 回滚 → 归档；单活跃计划由应用层保证；确认幂等（`uq_weekly_plan_user_run`） | `repositories/planning.py`；`/plans/*` 10 个端点 |
| 餐食管理 | CRUD + 换菜（LLM/确定性双路径）+ 营养达成报告 + 口味画像面板 | `/meals/*` 8 端点；`services/domain.py::replace_meal`；`services/nutrition.py` |
| 购物清单 | CRUD + 同类合并（中文单位归一：斤/两/克/升…换算后求和）+ 采购核销自动入库 + 实付价偏差情感判定（±10% 阈值） | `/shopping/*`；`services/unit_conversion.py`；`router.py::_price_sentiment` |
| 预算与支出 | 预算摘要、支出 CRUD、分类/月度趋势分析、85% 预警、历史区间查询 | `domain.py::budget_analytics`；`/budget/*` 5 端点 |
| 画像与营养目标 | `GET/PUT /profile` + `POST /profile/nutrition-goal`；Mifflin-St Jeor BMR → TDEE → 宏量分配；TDEE 钳制 [1000, 5000] | `router.py` L858-930；`services/nutrition.py::compute_nutrition_goal` |

#### 3.2.3 AI / Agent 能力（实现度 75%）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| LangGraph 规划工作流 | 11 节点 StateGraph（Phase 2 已移除 task/calendar 节点）：意图 → 双路并行检索 → 协调 → 3 领域智能体并行 → 领域协调 → 计划生成 → 校验 → 终稿；每步产出带耗时/状态/输出的 `AgentStep` 轨迹 | `ai/workflow.py::SoloChefWorkflow` |
| 结构化领域智能体 | 餐食/购物/预算三智能体，schema-bound JSON 输出（`response_format=json_object` + Pydantic 校验），LLM 失败自动确定性回退；运行模式串记录提示词版本（`llm:v1.2.0` / `deterministic:v1.2.0` / `deterministic-fallback:v1.2.0`） | `ai/domain_agents.py::StructuredDomainAgentEngine` |
| 提示词版本注册表 | 四智能体提示词语义化版本管理（meal 已迭代至 v1.2.0，含 taste_profile 输入契约与硬/软约束优先级规则），支持审计、回滚、A/B | `ai/prompts.py`；`/agents/prompts` 端点 |
| Verifier 确定性校验 | 预算钳制（estimated 超限时压回上限并重算 saved/usage）、忌口词命中（含「不吃辣→辣椒/辣酱/麻辣」等别名展开）、7 天餐食完整性、重复菜品、分类限额+预留≤总额、任务-日程时间重叠 | `workflow.py::_verifier_node` |
| Agent Run 追踪与恢复 | run 全状态持久化（status/steps/sources/error_type/failed_step/逐步 checkpoint）；失败 run 支持 retry（PostgreSQL 下走 LangGraph 断点续跑，其余方言退化为完整重跑） | `planning.py::{generate,resume}`；`/agents/runs/*` |
| 智能体离线评测 | 餐食硬约束满足率（70%）+时长合规（30%）、购物食材覆盖率、任务分配率、预算贴合度，加权 35/25/25/15 得综合分 | `ai/evaluation.py::evaluate_plan`；`/agents/evaluate` |
| 多轮对话流式规划 | 会话 CRUD；SSE 流式（step/token/complete/cancelled/error 五类事件）；`SummaryStreamExtractor` 从 JSON 流中实时抽取 summary 字段逐字输出；断线经 `/chat/sessions/{id}/events?after=` 重放恢复；用户可取消 | `services/conversation.py`；`services/runtime.py`（Redis 事件日志） |

#### 3.2.4 RAG 与知识库（实现度 80%）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| 向量检索 | 首阶段召回 top_k×3 候选 → bge-reranker-v2-m3 精排回 top_k；user_id 元数据过滤；cosine HNSW | `knowledge.py::retrieve_vector`；`reranker.py` |
| 嵌入双轨 | BGE-M3（1024 维）与内置 MiniLM（384 维）自动切换，维度隔离到独立 collection（`solochef_knowledge` / `-bge-m3` 后缀）；模型仅本地加载（`local_files_only`），绝不隐式下载 | `embeddings.py`；`vector_store.py::collection_name_for` |
| 图谱检索 | User/Preference/Constraint/Event/Recipe/Ingredient/Task/Plan/Budget/Document/KnowledgeEntity/FeedbackSignal 12 类节点；QuerySpec 结构化约束 Cypher（关键词+实体类型+关系白名单，LIMIT 40）+ 反馈子图查询（LIMIT 8）合并返回 | `graph_store.py::search` |
| 知识入库 | 文本/PDF/MD 上传（≤10MB，GB18030 兼容）；中文友好切块（600 字/重叠 100，分隔符含 `。；，`）；同步与 Celery 异步双通道；幂等键防重 | `services/documents.py`；`/knowledge/*`；`worker.py` |
| 查询改写与实体抽取 | LLM 级（temp 0，枚举白名单约束输出）+ 规则级（关键词映射表）双级降级 | `query_rewriter.py`；`entity_extractor.py` |
| 检索质量观测 | `/ai/status` 组件心跳；`/admin/rag/sync` Chroma↔Neo4j 一致性快照；`/admin/rag/eval` 5 用例离线评测输出 Recall@k / nDCG@k | `knowledge.py::{status,consistency_report}`；`rag_eval.py` |

#### 3.2.5 执行反馈闭环（实现度 85%）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| 反馈捕获 | 五类信号（任务完成/餐食替换/餐食评价/采购核销/支出记录）统一 `FeedbackSignal` 规范化；情感判定三级（评分优先 → 中文正负短语计票 → 中性）；口味标签词典抽取 | `feedback_loop.py::{capture,classify_sentiment,extract_taste_tags}` |
| 双库回流 | 图谱：`(User)-[:HAS_FEEDBACK]->(FeedbackSignal)-[:SIGNALS{polarity}]->(Preference)`；向量：按反馈类型维护固定 document_id 的滚动文档（窗口 30 条自然语言叙述），防知识库刷屏 | `feedback_loop.py::{_push_graph,_push_vector}` |
| 补偿重放 | 外部依赖失败仅置同步标记，`/feedback/resync` 批量重放；反馈总览含情感分布与待同步数 | `feedback_loop.py::replay`；`/feedback` 端点 |
| 口味画像学习 | 近 40 条餐食反馈 + 菜谱点赞聚合成净票数正/负标签、喜好/拒绝菜品、近期原话；同时注入 LLM 提示词（v1.2.0 契约）与确定性回退排序 | `repositories/feedback.py::taste_profile`；`domain_agents.py::meal` |

#### 3.2.6 基础设施与前端工程（实现度 70%）

| 模块 | 关键特性 | 技术实现 |
|---|---|---|
| 异步任务 | Celery 4 队列隔离（default/knowledge/graph/maintenance）；指数退避重试 ×3 后入死信表；beat 每日 03:00 清理 30 天前终态任务；任务可取消（revoke SIGTERM）；监控快照端点 | `worker.py`；`/admin/celery/stats`、`/jobs/*` |
| Redis 运行时 | 仪表盘 30s 缓存 + 写操作前缀失效；滑动窗口限流（chat 20 次/分、plan 30 次/分）；SSE 事件日志（1h TTL）；幂等键槽位锁；Redis 不可用时进程内降级 + 5s 熔断 | `services/runtime.py` |
| 前端工程 | 11 个懒加载视图（去家庭化后：4 删 2 新）；chunk 失败自动重试 + 整页重载兜底；axios 拦截器 401 自动刷新重放；SSE 手解流 + 断线重放（≤3 次）；Pinia 会话持久化（`solochef-session`）；路由/类型/品牌全量 SoloChef 化 | `frontend/src/router.ts`、`api.ts`、`stores/app.ts` |
| 容器化 | 7 服务 compose 编排（healthcheck 依赖序）；后端启动先 `alembic upgrade head` 再 uvicorn；前端多阶段构建 + nginx SPA 回退与 /api 反代 | `docker-compose.yml`；`backend/Dockerfile`；`frontend/Dockerfile` |

### 3.3 未实现功能说明

#### 3.3.1 阻断 MVP 闭环的缺口（P0）

| 编号 | 功能 | 具体需求 | 优先级 | 技术难点 | 现状代码证据 |
|---|---|---|---|---|---|
| G01 ✅ | ~~用户画像与营养目标 API~~ 已完成 | ~~新增 `GET/PUT /profile`、`POST /profile/nutrition-goal`~~ ✅ `router.py` L858-930 全部实现；`nutrition.py` L67-114 `compute_nutrition_goal` 完整实现 Mifflin-St Jeor BMR → TDEE → 宏量分配，含钳制 [1000, 5000]；`planning.py` L62-77 从 DB 读取目标注入工作流 | ~~**P0**~~ ✅ | ✅ 边界校验已实现（TDEE 钳制） | ✅ `/meals/nutrition` 返回用户真实目标值 |
| G02 ✅ | ~~三餐生成的营养硬约束~~ 已完成 | ~~Verifier 增加营养达成率校验~~ ✅ `workflow.py` L535-554 第 6 项校验：`estimate_meal_nutrition` 汇总实际营养 → 逐项对比 target → [90%, 110%] 越界写 warning | ~~**P0**~~ ✅ | ✅ 单餐营养估算已接入 Verifier | ✅ 故意偏离目标的输入产生 warning |
| G03 | ~~前端去家庭化~~ ✅ 已完成 | ~~品牌/导航/路由/文案改为 SoloChef~~ | **已完成** | 2026-08-08 完成验收 | `router.ts` 11 条路由全 SoloChef 定位；4 个家庭页面已删除；`types.ts` 已移除全部 Family* 类型；`AppShell.vue` 品牌已改 SoloChef |
| G04 ✅ | ~~配置漂移修复~~ 已完成 | ~~`.env` / `.env.example` 改为 MySQL~~ ✅ 均为 `mysql+aiomysql://solochef:...@localhost:3306/solochef` | ~~**P0**~~ ✅ | ✅ | ✅ `pytest` 无需临时覆盖 `DATABASE_URL` 即可跑 |
| G05 ✅ | ~~LangGraph checkpoint 方言适配~~ 已完成 | ~~MySQL 下恢复断点续跑~~ ✅ 已迁移至 `InMemorySaver`，移除 `PostgresSaver` / `psycopg` 全部 PostgreSQL 代码；生产持久化可后续引入 `langgraph-checkpoint-redis` | ~~**P0**~~ ✅ | ~~`langgraph-checkpoint-postgres` 与 psycopg 池深度绑定~~ 已移除依赖 | ✅ `planning.py::_configure_checkpointer` 移除方言判断；MySQL 下断点续跑可用 |

#### 3.3.2 影响可信度的短板（P1）

| 编号 | 功能 | 具体需求 | 优先级 | 技术难点 |
|---|---|---|---|---|
| G06 ✅ | ~~食材营养基准库~~ 已完成 | ~~扩充 `_INGREDIENT_NUTRITION`（10 种 → 200+ 种，对齐《中国食物成分表》）~~ ✅ 外置于 `app/data/ingredient_nutrition.json`，扩充至 **105 种**（101 verified + 4 estimated，对齐《中国食物成分表第6版》+ USDA）；`load_ingredient_nutrition()` 惰性加载 + `lru_cache`；`estimate_meal_nutrition` 返回 `is_calibrated` 标志，`build_nutrition_report` 报告 `calibrated_meals` / `uncalibrated_meals` | ~~**P1**~~ ✅ | ✅ 数据外置 JSON，校准状态字段化 | ✅ `/meals/nutrition` 报告标注校准餐数 |
| G07 | 食材替换营养联动 | 换菜后重算单餐/全天营养差异并联动更新购物清单增删改，前端展示替换前后对比 | **P1** | 依赖 G06；替换 diff 与购物合并的联动事务设计 |
| G08 | 购物替代建议真实化 | 当前 `/shopping/{id}/substitutions` 是 4 条硬编码字典（`router.py:1048`），应改为查询图谱 `Ingredient` 替代关系 + 同类营养近似排序 | **P1** | 需在图谱中建模「可替代」关系及替代方向性 |
| G09 ✅ | ~~种子知识库 SoloChef 化~~ 已完成 | ~~`BOOTSTRAP_DOCUMENTS` 3 篇改写~~ ✅ 已改为独居快手晚餐指南/控糖饮食原则/独居备餐与食材复用；`rag_eval.py::EVAL_SET` 5 条评测用例同步改写 | ~~**P1**~~ ✅ | ✅ 评测集与种子文档主题已对齐 |
| G10 ✅ | ~~Demo 数据 SoloChef 化~~ 已完成 | ~~`demo_data.py` 家庭叙事~~ ✅ EVENTS/TASKS/MEALS/SHOPPING/DOCUMENTS/BUDGET/Dashboard 全量去家庭化（assignee→"本人"、份量缩减一人食、标签"儿童友好"→"一人食"） | ~~**P1**~~ ✅ | ✅ demo 模式无家庭叙事 |
| G11 | 执行反馈复盘页 | 前端新增复盘视图：口味画像变化、预算偏差趋势、营养达标率周对比（消费 `/feedback` + `/meals/nutrition`） | **P1** | 纯前端 + 已有 API；ECharts 趋势图 |
| G12 ✅（部分） | ~~前端遗留模块冻结~~ + ~~工作流节点裁剪~~ ✅ | 前端去家庭化已完成（G03）；工作流 `task_agent` / `calendar_agent` 节点已移除（13→11 节点）；遗留 6 张表清理待 Phase 3 | **P1**（表清理降为 P2） | ✅ 工作流裁剪已完成；表清理需 Alembic 迁移 |

#### 3.3.3 工程化缺口（P1-P2）

| 编号 | 功能 | 具体需求 | 优先级 | 技术难点 |
|---|---|---|---|---|
| G13 ✅ | ~~Git 仓库与 CI/CD~~ 已完成 | ~~初始化 git、接入 GitHub Actions~~ ✅ `main` 分支已建立，Phase 3 提交 `738e886` 落地；`.github/workflows/ci.yml` 四道门禁（ruff / mypy / pytest / alembic 迁移），PR 与 push 到 main 触发 | ~~**P1**~~ ✅ | ✅ | ✅ `git log` 有提交记录；CI 配置就绪 |
| G14 | MySQL 集成测试 | docker-compose 起真实 MySQL 跑迁移 + 核心 API smoke suite | **P1** | aiomysql 与 aiosqlite 行为差异（如 `server_default`、JSON 默认值）需逐一核对 |
| G15 | 前端测试体系 | 引入 Vitest + 组件测试，至少覆盖 api.ts 拦截器与 router 守卫 | **P2** | 从零搭建；当前前端零测试零 lint |
| G16 | 女性生理周期饮食标记 | 自愿开启、非医疗化的周期记录与饮食提示（PRD F8） | **P2** | 隐私敏感设计；明确排除在 MVP 外 |

### 3.4 完成度统计

| 类别 | 已完成模块数 | 未实现/缺口数 | 结论 |
|---|---:|---:|---|
| 后端业务与 AI | 32 | 1（G07/G08 部分） | 最强资产；Phase 1/2/3 全部完成（G01/G02/G04/G05/G06/G09/G10/G12/G13 ✅），剩余 G07/G08 深化项 |
| RAG/知识库 | 7 | 1（G08） | 链路完整，种子库已换血（G09 ✅） |
| 前端 | 8 | 2（G07/G11） | 去家庭化已完成（G03 ✅），剩余为后端 API 对接与可视化增强 |
| 工程化 | 6 | 2（G14/G15） | 质量门禁全绿 + ✅ git 仓库 + CI 四道门禁就绪（G13 ✅）；缺 MySQL 集成测试与前端测试 |

---

## 四、工作流设计

本章描述项目开发、测试、部署的完整工作流程。**4.1-4.3 为当前实际执行的工作流**（代码与配置实证），**4.4-4.6 为目标设计**（当前缺口 + 落地方案）。

### 4.1 开发工作流（现状）

```mermaid
flowchart TD
    A[需求/定位 PRD] --> B[本地开发 PyCharm]
    B --> C{改动类型}
    C -->|后端| D[uv 依赖管理]
    C -->|前端| E[npm 构建]
    D --> F[质量门禁: ruff/mypy/pytest]
    E --> G[构建验证: npm run build]
    F --> H[接口联调: Yaak]
    G --> H
    H --> I[文档同步]
```

关键实践（实证）：
- **依赖管理**：后端用 uv（`pyproject.toml` 四个 extra：`ai`/`bge`/`rerank`/`dev`，AI 依赖按需安装）；`uv.lock` 冻结 + `uv export` 出 `requirements.txt` 供 Docker 使用。
- **环境分层**：`config.py` 默认值即开发环境（localhost MySQL/Redis/Neo4j/Chroma）；`.env` 覆盖；docker-compose 注入生产值；测试用环境变量切 SQLite 内存库。
- **PyCharm 运行配置**：`.run/` 下 5 个共享配置（Backend Dev / Debug / Frontend / Full Stack / DB Migration），新人导入即可跑。
- **接口调试**：Yaak 测试集 + `docs/PyCharm调试与Yaak测试.md` 操作手册。
- **双模式开发**：`LLM_PROVIDER=demo` 时全链路走确定性实现（`DemoPlanGenerator` + 领域智能体 fallback），无 API Key 也可完整开发与测试。

### 4.2 测试工作流（现状）

| 层级 | 工具与配置 | 覆盖内容 | 现状 |
|---|---|---|---|
| 接口/服务测试 | pytest + pytest-asyncio（`asyncio_mode=auto`），SQLite 内存库 | `test_api.py`（907 行：认证/计划/餐食/购物/反馈/知识/对话/后台任务）、`test_rag.py`（419 行）、`test_graph_rag_quality.py`（169 行） | **51 passed** |
| 静态检查 | ruff（E/F/I/UP/B/SIM，行宽 100）+ mypy 严格模式（`disallow_untyped_defs`，pydantic 插件，AI 可选依赖 `ignore_missing_imports`） | 全部 54 个源文件 | 全绿 |
| 前端类型 | `vue-tsc -b` 随 build 执行 | 26 个 TS/Vue 文件 | 通过 |
| 检索质量回归 | `/admin/rag/eval` 离线评测（Recall@k / nDCG@k，5 用例） | 检索链路 | 可用，需接入定期执行 |
| 智能体质量回归 | `/agents/evaluate` 四维度加权评分 | 计划产出质量 | 可用，需接入定期执行 |
| 冒烟 | `/health`、`/ai/status`、`/ai/llm/smoke` | 部署后存活与 LLM 连通 | 可用 |

**已知测试债**：前端零测试（无 Vitest/ESLint）；MySQL 方言仅靠 SQLite 近似覆盖（G14）；`.env` 漂移使「克隆即跑测试」不成立（G04）。

### 4.3 部署工作流（现状）

```mermaid
flowchart TD
    A[docker compose up -d] --> B[mysql:8.0 healthcheck]
    A --> C[redis:7-alpine]
    A --> D[neo4j:5-community]
    A --> E[chroma:1.5.9]
    B -->|healthy| F[backend 启动]
    C --> F
    D --> F
    E --> F
    F --> F1[alembic upgrade head + create_all 兜底]
    F1 --> F2[uvicorn :8000]
    F --> G[worker celery concurrency=2]
    F2 --> H[frontend vite build -> nginx :80]
    H --> H1["SPA try_files + /api 反代"]
```

- **启动顺序**：依赖健康检查后启动 backend；建表双保险（Alembic 正规迁移 + lifespan 幂等 `create_all` 兜底，`main.py:16`）。
- **数据持久化**：4 个命名卷（mysql_data / redis_data / neo4j_data / chroma_data）。
- **运维面**：`/admin/celery/stats` 队列监控、`/jobs/dead-letter` 死信、`/jobs/cleanup` 手动清理（另有 beat 每日自动）、`/admin/rag/sync` 一致性巡检。
- **本地零配置降级**：`DATABASE_URL=sqlite+aiosqlite:///./solochef.db` 即可脱离 Docker 全栈开发。

### 4.4 版本控制策略（现状 + 目标）

**现状**：项目目录**已是 git 仓库**（`main` 分支），已有 Phase 3 完成提交（`738e886`）。`.gitignore` 已配置（Python/前端/密钥/日志/SQLite 本地库全覆盖）。G13 ✅ 已完成。

**目标策略（Trunk-Based 轻量版，适配个人项目 + AI 协作）**：

| 项 | 策略 |
|---|---|
| 分支模型 | `main` 受保护（仅 PR 合入）+ 短生命周期 `feature/<scope>-<desc>` 分支；hotfix 走 `fix/*` |
| 提交规范 | Conventional Commits（`feat/fix/refactor/docs/test/chore`），与里程碑标签对应 |
| 版本号 | SemVer；后端 `pyproject.toml` 与报告头部版本联动；提示词版本（`prompts.py`）独立语义化演进 |
| 数据迁移纪律 | 每次模型变更必须配套 Alembic revision（`alembic revision --autogenerate` + 人工复核），禁止裸改模型依赖 `create_all` |
| PR 粒度 | 单 PR ≤ 400 行有效变更；涉及提示词/检索策略变更必须附 `/agents/evaluate` 或 `/admin/rag/eval` 前后对比 |

### 4.5 代码审查机制（目标设计）

个人项目采用「**门禁自动化 + AI 结对审查 + 人工终审**」三层：

1. **自动化门禁（合入前必过）**：ruff / mypy / pytest / 前端 build 四道关卡，任一红灯禁止合入；
2. **AI 结对审查**：每个 PR 由 AI 助手按固定清单审查——分层依赖方向（api→services→repositories，禁止反向）、异步阻塞调用是否 `asyncio.to_thread` 包裹、外部依赖失败是否只降级不阻断（feedback_loop 模式）、幂等与事务边界；
3. **人工终审聚焦**：产品定位符合性（拒绝家庭域回潮）、提示词变更的语义审查、安全面（密钥不落库、日志不打印凭证）。

### 4.6 CI/CD 流水线（已落地）

```mermaid
flowchart TD
    A[PR 提交 / push main] --> B[ruff check lint]
    A --> C[mypy app typecheck]
    A --> D[pytest SQLite 内存库 test]
    A --> E[alembic upgrade head migrations]
    B --> F{全部通过?}
    C --> F
    D --> F
    E --> F
    F -- 否 --> G[阻止合入]
    F -- 是 --> H[合入 main]
    H --> I[docker build 镜像]
    I --> J[compose pull and up]
    J --> K[health 冒烟]
    K -- 失败 --> L[回滚上一版本]
```

**落地状态**（`.github/workflows/ci.yml`，G13 ✅）：四个 job 并行跑，任一失败阻断合并——
1. `lint`：`ruff check app/ tests/`（独立装 ruff，轻量）；
2. `typecheck`：`pip install -e ".[dev]"` 后 `mypy app/ --ignore-missing-imports`；
3. `test`：注入 `DATABASE_URL=sqlite+aiosqlite:///./test.db` 跑 `pytest -q`，失败上传 `.pytest_cache` 产物；
4. `migrations`：`alembic upgrade head` 验证迁移链可跑通（downgrade 因 0002 遗留表模型已删无法重建，`continue-on-error` 容忍）。

后续增强：nightly 用 GitHub Actions `services:` 起 MySQL 8 / Redis / Neo4j / Chroma 容器跑集成测试（G14）；镜像标签与 git tag 一致保证可回滚。CI 环境显式注入 `DATABASE_URL=sqlite+aiosqlite:///:memory:`（同时根治 G04 的本地漂移）。

---

## 五、Agent 设计流程

### 5.1 总体架构：一张可执行的 LangGraph 状态图

项目的 Agent 体系是一个**「协调器 + 领域专家 + 校验器」三层编排的可执行状态机**（`ai/workflow.py::SoloChefWorkflow`，11 节点），不是简单的单次 LLM 调用：

```mermaid
flowchart TD
    A([START]) --> B[Intent Agent 意图识别]
    B --> C[Graph Retriever 图谱检索]
    B --> D[Vector Retriever 向量检索]
    C --> E[Coordinator 上下文融合]
    D --> E
    E --> G[Meal Agent 餐食策略]
    E --> H[Shopping Agent 购物合并]
    E --> J[Budget Agent 预算限额]
    G --> K[Domain Coordinator 领域结果合并]
    H --> K
    J --> K
    K --> L[Planning Agent LLM 生成]
    L --> M[Verifier 确定性校验]
    M --> N[Final Planner 汇总输出]
    N --> O([END])
```

**并行设计**：双路检索并行（START 后扇出）、三个领域智能体并行（coordinator 后扇出），LangGraph 的 `Annotated[list, operator.add]` 归约器自动合并并行分支的 `trace` / `domain_results` / `specialist_outputs`。Phase 2 已移除 `calendar_agent` / `task_agent` 节点（13→11），`_empty_calendar_result()` 兼容垫片保留供 `confirm_plan` 端点的 `analyze_calendar` 调用。

### 5.2 核心功能模块

| 模块 | 输入 → 输出 | 决策职责 | 实现 |
|---|---|---|---|
| Intent Agent | `PlanningRequest` → intent dict | 识别规划类型与硬约束清单 | `workflow.py::_intent_node` |
| Graph/Vector Retriever | prompt → `GraphSearchHit[]` / `VectorSearchHit[]` + 状态 | 硬约束（图谱）与软知识（向量）分工召回 | `knowledge.py::{retrieve_graph,retrieve_vector}` |
| Coordinator | 两路 hits → context 文本 | 把检索结果排版为「知识图谱：… 向量知识片段：…」的可注入上下文 | `workflow.py::_coordinator_node` |
| 三领域智能体 | request + 画像 + 日程 (+taste_profile) → 各 schema 结果 | 各自产出**结构化中间约束**而非最终计划（见 5.3） | `domain_agents.py::StructuredDomainAgentEngine`（meal/shopping/budget 三智能体） |
| Domain Coordinator | 三路 dict → `DomainAgentBundle` | 反向 Pydantic 校验 + 合并为去重约束清单（时长上限、预算预留、公平规则、采购策略） | `workflow.py::_domain_coordinator_node` |
| Planning Agent | context + 专家建议 + 领域约束 → `PlanDraft` | 唯一与 LLM 自由生成对话的节点；流式输出 | `ai/llm.py::OpenAICompatiblePlanGenerator` |
| Verifier Agent | draft + 约束 → warnings + 修正后 draft | **确定性**后校验（不调 LLM），可直接改写预算字段 | `workflow.py::_verifier_node` |
| Final Planner | 全状态 → sources + 汇总 | 标注每条计划的知识来源（Neo4j / Chroma 文档名 / 降级声明） | `workflow.py::_final_node` |

### 5.3 决策逻辑：约束式生成的三段式

本项目区别于「一次性 prompt 生成」的核心设计是**前约束、中约束、后校验**三段：

**① 前约束（检索注入）**：Graph Retriever 召回的 `HAS_CONSTRAINT`（忌口/过敏）与向量知识片段，由 Coordinator 排版进上下文；`PlanningService._load_taste_profile` 额外把历史反馈聚合的口味画像注入状态，使长期偏好进入本轮决策。

**② 中约束（结构化中间结果）**：三个领域智能体各自只产出狭窄 schema（如 `MealAgentResult{strategy, constraints_applied, excluded_ingredients, preferred_tags, max_duration_minutes}`），LLM 路径用 `response_format=json_object` + Pydantic 强校验，任何异常立即切换**确定性回退实现**——回退不是摆设：口味学习在回退路径同样生效（`merged_tags = liked + preferences − disliked`；排除项 = 硬约束 ∪ 负向标签 ∪ 被拒菜品）。运行模式串（`llm:v1.2.0` / `deterministic:v1.2.0` / `deterministic-fallback:v1.2.0`）把所用提示词版本写进每次 trace，可审计、可回归。

**③ 后校验（确定性 Verifier）**：LLM 产出之后必经八道确定性检查——预算超限钳制（`estimated` 压回上限并重算 saved/usage_percent）、忌口词别名展开命中（`不吃辣→{辣椒,辣酱,麻辣}`、`X过敏→X`、`忌X→X`）、7 天餐食完整性、重复菜品、预算分类合计、领域限额+预留≤总额、未知任务负责人、任务-日程时间重叠。警告不阻断输出但随计划持久化，前端可见。

**口味学习决策规则**（`prompts.py` meal v1.2.0 契约）：硬约束（过敏/忌口）不可协商；口味偏好是软约束、冲突时让位硬约束；样本量为 0 时退回静态画像；正反馈进 `preferred_tags`、负反馈进 `excluded_ingredients` 或从偏好剔除。同标签正负信号冲突时按净票数归属（`feedback.py::taste_profile`）。

### 5.4 与其他系统组件的交互

```mermaid
flowchart TD
    WF[工作流 SoloChefWorkflow] --> KS[知识检索 Neo4j/Chroma]
    WF --> FR[反馈仓储 MySQL]
    WF --> AR[(agent_runs 表)]
    ENG[领域智能体] --> LLM[DeepSeek API]
    GEN[计划生成器] --> LLM
    GEN --> DEMO[demo 兜底数据]
    WF --> SSE[SSE Redis 前端]
    AR --> RETRY[/retry 续跑或重跑/]
```

| 交互对象 | 方式 | 容错 |
|---|---|---|
| MySQL | `PlanningRepository` 创建/更新 run 记录；每步 checkpoint 落库 | 无 session 时纯内存运行（测试路径） |
| Neo4j/Chroma | 经 `KnowledgeRetriever` Protocol 注入，可替换 | 不可用时 hits 为空、trace 标 WARNING，流程继续 |
| LLM | langchain-openai `ChatOpenAI`，OpenAI 兼容协议 | `LLMGenerationError` → `ai_fallback_enabled` 时 DemoPlanGenerator 兜底，mode 记为 `x->demo-fallback` |
| Redis | SSE 事件日志、取消标记、限流 | 进程内降级 + 5s 熔断（`runtime.py`） |
| LangGraph checkpoint | `InMemorySaver`（进程内缓存，不绑定数据库方言） | ✅ MySQL 下断点续跑可用；生产持久化可后续引入 `langgraph-checkpoint-redis`（G05 ✅ 已完成） |

### 5.5 技术实现细节

- **状态模型**：`WorkflowState`（TypedDict，total=False）承载 request/检索结果/领域结果/draft/trace 等 17 个键；并行分支用 `operator.add` 归约。
- **流式桥接**：LLM token 经 `ContextVar[TokenSink]`（`llm.py::token_sink`）穿透到 `ConversationService.on_token`；`SummaryStreamExtractor` 用有限状态机从 JSON 流中只抽取 `"summary"` 字段的字符串值逐字推给前端——避免了「先看到一堆 JSON 再看到结论」的糟糕体验。
- **可取消**：每个 step/token 回调先查 Redis 取消标记，命中即抛 `PlanningCancelledError` / `CancelledError`，run 记录落 `failed + CancelledError + failed_step`。
- **可恢复**：`resume()` 从 `agent_runs.checkpoint` 还原，配合 LangGraph `aget_state(thread_id)` 断点续跑；恢复后 checkpoint 标记 `resumed: true`。
- **观测面**：`/agents/runs` 列表与详情、`/agents/prompts` 提示词注册表、`/agents/evaluate` 四维加权评分（餐食 35/购物 25/任务 25/预算 15）。

### 5.6 已知局限

1. ~~**无营养硬约束**：Verifier 八项校验不含热量/宏量达成率（G02）~~ ✅ 已解决：Verifier 第 6 项营养达成率校验 [90%, 110%]；
2. ~~**checkpoint 方言绑定**：MySQL 迁移后断点续跑失效（G05）~~ ✅ 已解决：迁移至 `InMemorySaver`；
3. ~~**遗留智能体稀释**：calendar/task/budget 三智能体仍带家庭语义~~ ✅ 已解决：`task_agent` / `calendar_agent` 节点已移除（13→11 节点），Demo 数据全量去家庭化；
4. ~~**Demo 数据出戏**：无 LLM 时演示数据仍是家庭叙事（G10）~~ ✅ 已解决：Demo 数据全量换血为独居膳食向；
5. **Planner 单点自由生成**：最终 PlanDraft 完全由一次 LLM 调用产出，7 天×3 餐的结构稳定性依赖 prompt 约束 + Verifier 兜底，尚无分段生成/重试细化策略。

---

## 六、RAG（检索增强生成）设计流程

### 6.1 技术架构：Graph + Vector 双引擎混合检索

```mermaid
flowchart TD
    UP[文本/MD/PDF 上传] --> DP[中文切块 600/重叠100]
    DP --> VS[(Chroma 向量库)]
    DP --> EE[实体关系抽取]
    EE --> GS[(Neo4j 图谱)]
    FB[执行反馈] --> RN[滚动叙述文档]
    RN --> VS
    FB --> GS
    Q[用户查询] --> QR[查询改写]
    QR --> V1[向量召回 top_k×3]
    V1 --> RR[二阶段精排 top_k]
    QR --> G1[图谱 Cypher 查询]
    V1 --> MERGE[Coordinator 融合]
    G1 --> MERGE
    RR --> MERGE
    MERGE --> CTX[RAG 上下文]
    CTX --> PLAN[生成模型]
```

**分工原则**：图谱管**硬约束与关系**（忌口、偏好、食材-菜谱、反馈信号），向量库管**非结构化语义知识**（菜谱文档、营养原则、反馈叙述）。两者并行召回（`asyncio.gather`），由 Coordinator 融合为统一文本上下文注入生成模型——即经典的 GraphRAG 混合架构，且**任一引擎不可用都降级为空召回而非失败**。

### 6.2 数据来源与知识库构建

| 来源 | 内容 | 入库方式 | 目标存储 |
|---|---|---|---|
| 内置种子文档 | `BOOTSTRAP_DOCUMENTS` 3 篇（独居快手晚餐指南/控糖饮食原则/独居备餐与食材复用，G09 ✅ 已换血） | `/knowledge/bootstrap` 幂等重建（固定 document_id） | Chroma + Neo4j |
| 用户上传 | 文本/MD/PDF，中文编码自动识别（utf-8-sig/utf-8/gb18030） | 同步端点或 Celery 异步 job（幂等键防重，死信兜底） | Chroma 切块 + Neo4j 实体关系 |
| 业务数据投影 | 菜谱/任务/计划/预算/日程 | 每次检索前 `sync_user_context` 全量 MERGE（先删后建 Member/Event 子图） | Neo4j |
| 执行反馈 | 五类反馈信号的自然语言叙述 + 结构化子图 | `feedback_loop` 捕获时即时回流；失败待补偿重放 | Neo4j 反馈子图 + Chroma 滚动文档 |

**切块策略**：`RecursiveCharacterTextSplitter`，chunk 600 字、重叠 100，分隔符按中文排版优先级 `## → ### → 空行 → 换行 → 。→ ；→ ，→ 空格`，保证菜谱步骤不被从句中切断。

**图谱 Schema**（12 类节点）：`User / Preference / Constraint / Event / Recipe / Ingredient / Task / Plan / Budget / Document / KnowledgeEntity / FeedbackSignal`；关键关系：`PREFERS`、`HAS_CONSTRAINT`、`REQUIRES`（菜谱→食材）、`MENTIONS`（文档→实体）、`RELATION`（LLM 抽取的自由三元组）、`HAS_FEEDBACK / ABOUT / SIGNALS{polarity}`（反馈子图）。所有节点带 `user_id` 属性隔离多用户。

### 6.3 检索策略

**四级流水线**（每级都可独立降级）：

1. **查询改写**（`query_rewriter.py`）：LLM 级把自然语言改写为 `QuerySpec{keywords, entity_kinds, relations}`（实体类型与关系限枚举白名单，temp 0）；失败回退规则级（6 类实体、5 类关系的关键词映射表 + 标点分词）。
2. **双路首阶段召回**：
   - 向量：Chroma `query`（user_id 元数据过滤，cosine 距离转 0-1 分），召回 `top_k × rerank_candidate_multiplier(3)` 候选；
   - 图谱：参数化 Cypher——关键词 CONTAINS + 实体类型白名单 + 关系白名单（恒含 `HAS_CONSTRAINT/AVOIDS`），LIMIT 40；另路反馈子图查询 LIMIT 8 置顶合并。
3. **二阶段精排**（`reranker.py`）：`bge-reranker-v2-m3` 对候选逐一算 (query, doc) 相关性分，重排后截回 top_k；模型缺失时透传首阶段排序，链路不断。
4. **融合注入**：Coordinator 把图关系（`主体 --关系--> 客体`）与向量片段（`[文档名#块号, score] 内容`）排版为分区文本，附带 `RetrievalDiagnostics`（chroma/neo4j/embedding/rerank 四组件状态）供前端与排障。

### 6.4 生成模型选型

| 组件 | 选型 | 理由 | 降级链 |
|---|---|---|---|
| 主生成 LLM | DeepSeek `deepseek-chat`（OpenAI 兼容，base_url 可换） | 中文场景性价比与 JSON 遵循度；temp 0.2 / max_tokens 4096 / `json_object` 强制 | LLM 异常 → `DemoPlanGenerator` 确定性计划（可关：`ai_fallback_enabled=false`） |
| 领域智能体 LLM | 同上，temp 0.1 / max_tokens 900（小 schema 快调用） | 结构化中间结果要求稳定 | 异常 → 各智能体内置确定性实现（模式串可审计） |
| Embedding | BGE-M3（1024 维，sentence-transformers 本地加载） | 中文语义检索开源 SOTA 梯队；`local_files_only` 杜绝隐式下载 | 缺模型/缺依赖 → Chroma 内置 ONNX MiniLM-L6-v2（384 维），**独立 collection 隔离维度** |
| Reranker | bge-reranker-v2-m3（FlagEmbedding 本地加载） | 与 BGE-M3 同族，精排增益明确 | 不可用 → 透传首阶段排序 |
| 查询改写/实体抽取 | 主 LLM temp 0 小输出 | 复用同一 provider，零额外成本 | 规则级关键词映射 / 正则「类型: 值」 |

### 6.5 性能优化与容错

- **全链路异步**：Chroma/Neo4j/模型加载等阻塞调用一律 `asyncio.to_thread` 移出事件循环（`vector_store.py`、`checkpoints.py`）；双路检索 `asyncio.gather` 并行。
- **进程内单例 + 锁**：embedding/reranker/Chroma collection 解析一次缓存（`ensure_embedding` / `ensure_reranker`，`asyncio.Lock` 防并发重复加载大模型）。
- **滚动文档防膨胀**：反馈向量按类型固定 document_id 覆写（窗口 30 条），知识库文档数与反馈次数解耦。
- **双库一致性自检**：`/admin/rag/sync` 对比 Chroma 文档清单与 Neo4j Document 节点，报「图谱未同步」与「孤儿节点」两类偏差。
- **容错总原则**：任何外部依赖（Neo4j/Chroma/Redis/LLM）异常只降级（空召回 / WARNING 状态 / 同步标记置 false / 补偿重放），**永不阻断主业务链路**——这一原则在 `knowledge.py`、`feedback_loop.py`、`runtime.py` 中一致贯彻。

### 6.6 检索质量评测

`/admin/rag/eval` 提供**不依赖 LLM 的离线回归评测**（`rag_eval.py`）：内置 5 条评测用例（query + 期望文档 + 期望实体类型），逐案计算 **Recall@k**（期望命中占比）与 **nDCG@k**（命中位置折扣增益），输出均值与逐案明细。评测集与种子文档主题已对齐（G09 ✅ 独居膳食向）。当前缺口：评测未接入定期执行，用例量偏少（5 条）。

---

## 七、下一步计划

### 7.1 总体策略

前端去家庭化已完成（G03 ✅），**Phase 1 营养闭环、Phase 2 技术债务清理、Phase 3 深化均已全部完成**（G01/G02/G04/G05/G06/G09/G10/G12/G13 ✅）。后端开发流程三个 Phase 全部落地，项目进入可交付状态。剩余可选深化项为 G07（食材替换营养联动）、G08（购物替代建议图谱化）、G11（前端复盘页）、G14（MySQL 集成测试）、G15（前端测试体系）。

| 优先级 | 任务 | 缺口 | 主线 | 状态 |
|---|---|---|---|---|
| **P0** ✅ | `.env` 配置漂移修复 | G04 | 安全性 + 工程化 | ✅ |
| **P0** ✅ | 画像/营养目标 API + TDEE 公式 | G01 | 业务逻辑 + 接口优化 | ✅ |
| **P0** ✅ | 营养硬约束 + Verifier 校验 | G02 | 业务逻辑 + 错误处理 | ✅ |
| **P1** ✅ | 工作流节点裁剪（移除 Task/Calendar） | G12 | 数据模型 + 性能优化 | ✅ |
| **P1** ✅ | LangGraph checkpoint MySQL 适配 | G05 | 错误处理 + 性能优化 | ✅ |
| **P1** ✅ | 种子知识库 + Demo 数据换血 | G09/G10 | 业务逻辑 | ✅ |
| **P2** ✅ | 遗留表清理（6 张） | G12 | 数据模型 | ✅ |
| **P2** ✅ | 食材营养基准库扩充 | G06 | 业务逻辑 | ✅ |
| **P2** ✅ | git 初始化 + CI/CD | G13 | 工程化 | ✅ |
| P2 | 食材替换营养联动 | G07 | 业务逻辑 | 待办（可选） |
| P2 | 购物替代建议图谱化 | G08 | 业务逻辑 | 待办（可选） |
| P2 | 前端复盘页 | G11 | 前端 | 待办（可选） |
| P2 | MySQL 集成测试 | G14 | 工程化 | 待办（可选） |
| P2 | 前端测试体系 | G15 | 工程化 | 待办（可选） |

### 7.2 后端开发路线图

```mermaid
gantt
    title SoloChef 后端完善路线图
    dateFormat YYYY-MM-DD
    axisFormat %m-%d

    section Phase 1 营养闭环（P0）✅
    .env MySQL 同步                :done, p1a, 2026-08-09, 0.5d
    画像/营养目标 API + TDEE        :done, p1b, 2026-08-09, 2d
    营养硬约束 + Verifier           :done, p1c, 2026-08-11, 1.5d
    pytest 补充                    :done, p1d, 2026-08-12, 0.5d

    section Phase 2 技术债务清理（P1）✅
    工作流裁剪 Task/Calendar       :done, p2a, 2026-08-13, 1d
    checkpoint MySQL 适配           :done, p2b, 2026-08-13, 0.5d
    种子库/Demo 数据换血            :done, p2c, 2026-08-13, 0.5d

    section Phase 3 深化（P2）✅
    遗留表清理                     :done, p3a, 2026-08-09, 1d
    食材营养库扩充                  :done, p3b, 2026-08-09, 1d
    git 初始化 + CI                :done, p3c, 2026-08-09, 0.5d
```

### 7.3 Phase 1：营养目标闭环（P0，3.5 天）✅ 已完成

| 序号 | 任务 | 缺口 | 实际工时 | 技术方案（实际实施） | 验收标准 |
|---|---|---|---|---|---|
| 1 ✅ | `.env` MySQL 同步 | G04 | 0.5h | `.env` / `.env.example` 均为 `mysql+aiomysql://solochef:...@localhost:3306/solochef`，与 config.py 默认值对齐 | ✅ `pytest` 无需临时覆盖 `DATABASE_URL` 即可跑 |
| 2 ✅ | 画像与营养目标 API | G01 | 6h | `router.py` L858-930 实现 `GET/PUT /profile` + `GET/POST /profile/nutrition-goal`；`nutrition.py` L67-114 `compute_nutrition_goal(profile)` 完整实现 Mifflin-St Jeor BMR（男 `10w+6.25h-5a+5`；女 `-161`）→ ×活动系数（久坐 1.2 / 轻度 1.375 / 中度 1.55 / 高度 1.725）→ 钳制 [1000, 5000] → 按目标调整（增肌 +10% / 减脂 -15% / 维护 0%）→ 分配 P/C/F（增肌 P30%C40%F30%；减脂 P40%C30%F30%；维护 P25%C50%F25%）；`planning.py` L62-77 `_load_nutrition_targets` 从 DB 读取目标注入工作流 | ✅ 录入身体数据后返回 BMR/TDEE/P/C/F；`/meals/nutrition` 返回用户真实目标值 |
| 3 ✅ | 营养硬约束 | G02 | 4h | `workflow.py` L535-554 Verifier 第 6 项校验「营养目标达成率」：用 `estimate_meal_nutrition` 汇总全天实际营养 → 逐项对比 target → 达成率 < 90% 或 > 110% 写 warning；`nutrition_targets` 通过 `WorkflowState` 注入（L46/L160/L184） | ✅ 故意偏离目标的输入产生 warning；`pytest` 60 passed 全绿 |
| 4 ✅ | pytest 补充 | — | 1h | 为 `/profile`、`/profile/nutrition-goal` 端点补用例；为 Verifier 营养校验补用例 | ✅ `pytest` 60 passed 全绿 |

**Phase 1 交付物**：✅ 后端营养目标闭环已打通（G01/G02/G04），`pytest` 60 passed 全绿，`/meals/nutrition` 返回用户真实目标值。

### 7.4 Phase 2：技术债务清理（P1，3 天）✅ 已完成

| 序号 | 任务 | 缺口 | 实际工时 | 技术方案（实际实施） | 验收标准 |
|---|---|---|---|---|---|
| 5 ✅ | 工作流节点裁剪 | G12 | 4h | `workflow.py` 移除 `task_agent` / `calendar_agent` 节点及对应边，保留 `_empty_calendar_result()` 兼容垫片（`confirm_plan` 端点仍调用 `analyze_calendar`）；13 节点 → 11 节点 | ✅ 工作流正常运行；`pytest` 60 passed 全绿；`test_rag.py` 断言 trace 长度 == 11 |
| 6 ✅ | checkpoint MySQL 适配 | G05 | 3h | `checkpoints.py` 移除 `PostgresSaver` / `AsyncThreadedPostgresSaver` / `psycopg` / `psycopg_pool` 全部 PostgreSQL 代码，改为 `InMemorySaver`（进程内缓存，不绑定数据库方言）；`planning.py::_configure_checkpointer` 移除 `dialect.name != "postgresql"` 方言判断，仅保留 session None 检查；`pyproject.toml` 移除 `psycopg[binary,pool]` 和 `langgraph-checkpoint-postgres` 依赖 | ✅ MySQL 下 `/agents/runs/{id}/retry` 走断点续跑；`pytest` 60 passed 全绿；`ruff` 全绿 |
| 7 ✅ | 种子库 + Demo 数据换血 | G09/G10 | 3h | `knowledge.py::BOOTSTRAP_DOCUMENTS` 3 篇改为独居快手晚餐/控糖原则/独居备餐与食材复用；`demo_data.py` 的 EVENTS/TASKS/MEALS/SHOPPING/DOCUMENTS/BUDGET/Dashboard 全量去家庭化（assignee 统一为"本人"、份量缩减为一人食、标签移除"儿童友好"改为"一人食"）；`rag_eval.py::EVAL_SET` 5 条评测用例同步改写；`query_rewriter.py` 添加"本人"到 Member 关键词；`entity_extractor.py` 系统提示词示例去家庭化；`feedback_loop.py` / `repositories/feedback.py` 文档注释 PostgreSQL → MySQL | ✅ bootstrap 后文档主题为独居膳食；demo 模式无家庭叙事；`pytest` 60 passed 全绿；`ruff` 全绿 |

**Phase 2 交付物**：✅ 工作流精简为 11 节点，checkpoint 在 MySQL 下通过 `InMemorySaver` 可用（移除 PostgreSQL 依赖），种子内容全量 SoloChef 化（含评测集与查询改写器同步更新）。`pytest` 60 passed / `ruff` 全绿。

### 7.5 Phase 3：深化与工程化（P2，2.5 天）✅ 已完成

| 序号 | 任务 | 缺口 | 实际工时 | 技术方案（实际实施） | 验收标准 |
|---|---|---|---|---|---|
| 8 ✅ | 遗留表清理 | G12 | 4h | `models/identity.py` 移除 6 张遗留表模型（calendar_events / calendar_event_exceptions / plan_tasks / plan_budgets / task_completions / inventory_items），数据模型精简为 14 张核心表；删除 `repositories/calendar.py`；`router.py` 移除 `/calendar/*` `/tasks/*` `/inventory/*` 端点（仅留注释标记）；新增 `alembic/versions/0002_drop_legacy_tables.py` 用 `DROP TABLE IF EXISTS` 按外键逆序删除 6 表 | ✅ 20 表 → 14 表；`pytest` 54 passed 全绿；`ruff` / `mypy` 全绿 |
| 9 ✅ | 食材营养基准库扩充 | G06 | 4h | 食材营养库外置于 `app/data/ingredient_nutrition.json`（**105 种**，101 verified + 4 estimated，对齐《中国食物成分表第6版》+ USDA FoodData Central）；`app/data/__init__.py::load_ingredient_nutrition` 惰性加载 + `lru_cache` 缓存；`nutrition.py::_INGREDIENT_NUTRITION` 改为调用加载器；`estimate_meal_nutrition` 返回 `is_calibrated` 标志（命中菜谱 True / 命中食材库 False）；`build_nutrition_report` 报告 `calibrated_meals` / `uncalibrated_meals` 计数 | ✅ `/meals/nutrition` 报告标注校准餐数；营养估算精度提升 |
| 10 ✅ | git 初始化 + CI | G13 | 2h | `git init` + `.gitignore`（Python/前端/密钥/日志/SQLite 本地库）+ 分支统一为 `main`（对齐 Trunk-Based 策略与 ci.yml 触发分支）；Phase 3 提交 `738e886` 落地；`.github/workflows/ci.yml` 四道门禁：`lint`(ruff) / `typecheck`(mypy) / `test`(pytest, SQLite 内存库) / `migrations`(alembic upgrade head)，四 job 并行，PR 与 push 到 main 触发 | ✅ `git log` 有提交记录；CI 配置就绪，PR 检查自动触发 |

**Phase 3 交付物**：✅ 数据模型精简为 14 表（alembic 0002 迁移），食材营养库扩充至 105 种（外置 JSON + 校准标注），git 仓库 + GitHub Actions CI 四道门禁就绪。`pytest` 54 passed / `ruff` 全绿 / `mypy` 54 源文件无问题。修复 `db/session.py` SQLite 方言连接池参数兼容（避免测试内存库启动失败）。

### 7.6 错误处理与安全性增强（贯穿各 Phase）

| 增强项 | 现状 | 目标 | 实施阶段 |
|---|---|---|---|
| **输入校验** ✅ | ~~`UserProfile` 字段无边界校验~~ ✅ 已实现 TDEE 钳制 [1000, 5000] | ✅ | Phase 1 序号 2 ✅ |
| **营养目标边界** ✅ | ~~TDEE 计算无极值保护~~ ✅ 已钳制到 [1000, 5000] kcal | ✅ | Phase 1 序号 2 ✅ |
| **Verifier 营养校验** ✅ | ~~8 项校验不含营养达成率~~ ✅ 第 6 项：热量/P/C/F 达成率 ∈ [90%,110%]，越界写 warning | ✅ | Phase 1 序号 3 ✅ |
| **checkpoint 异常恢复** ✅ | ~~MySQL 下静默退化为完整重跑~~ 已迁移至 `InMemorySaver`，MySQL 下断点续跑可用 | 生产持久化可后续引入 `langgraph-checkpoint-redis` | Phase 2 序号 6 ✅ |
| **遗留端点删除** ✅ | ~~`/calendar/*` `/tasks/*` `/inventory/*` 仍可访问但前端已不调用~~ ✅ 端点已移除（router 仅留注释标记），6 张遗留表经 alembic 0002 迁移删除 | ✅ | Phase 3 序号 8 ✅ |
| **`.env` 漂移** ✅ | ~~`.env.example` 仍为 PostgreSQL 连接串~~ ✅ 已对齐 MySQL | ✅ | Phase 1 序号 1 ✅ |

### 7.7 降级策略

1. **G05 checkpoint 适配** ✅ 已完成：采用 `InMemorySaver` 替代 PostgreSQL saver，进程内缓存支持断点续跑；生产环境如需跨进程持久化，可引入 `langgraph-checkpoint-redis`（项目已依赖 `redis` 客户端）；
2. **G06 食材营养库**：若 Phase 3 时间不足，沿用现有 10 种食材，营养报告标注「未校准」；
3. **G07 替换重算 / G08 购物替代**：本周期不改，沿用现有换菜逻辑和硬编码替代字典；
4. **CI/CD 完整版**：若 Phase 3 时间不足，只做 `git init` + 本地三道门禁，不接 GitHub Actions；
5. **女性生理周期（G16）**：明确排除在本周期外。

---

## 八、附录

### 8.1 数据库表清单（14 张，`models/identity.py`）

Phase 3 清理后 6 张遗留表已全部移除（alembic 0002 迁移），数据模型精简为 14 张核心表：

- `users`、`refresh_sessions`、`user_profiles`、`nutrition_goals`、`weekly_plans`、`plan_meal_items`、`plan_shopping_items`、`recipes`、`expense_records`、`plan_feedback`、`chat_sessions`、`chat_messages`、`agent_runs`、`background_jobs`

~~遗留表（6，已删除）~~：`calendar_events` / `calendar_event_exceptions` / `plan_tasks` / `plan_budgets` / `task_completions` / `inventory_items`（G12 ✅ Phase 3 已清理）

### 8.2 API 端点分组（87 个，`api/router.py` 75 + `api/auth_router.py` 12）

Phase 3 清理后移除日程 8 / 任务 7 / 库存 3 共 18 个遗留端点，新增画像/营养目标 3 个端点：

认证 12 · 仪表盘/健康/AI 状态 4 · 画像/营养目标 3 · 计划 10 · 餐食 8 · 购物 7 · 预算 5 · 反馈 2 · 菜谱 4 · 知识库 4 · 后台任务 4 · 监控管理 3 · 对话 8 · Agent 5 · 营养报告 1（`/meals/nutrition`）。

### 8.3 代码依据索引

| 主题 | 文件 |
|---|---|
| 工作流编排 | `backend/app/ai/workflow.py` |
| 领域智能体 / 提示词注册表 / LLM 生成器 / 评测 | `backend/app/ai/{domain_agents,prompts,llm,evaluation}.py` |
| 规划服务（持久化/恢复/口味注入） | `backend/app/services/planning.py` |
| RAG 编排 / 向量库 / 图谱 / 嵌入 / 精排 / 改写 / 抽取 / 评测 | `backend/app/services/{knowledge,vector_store,graph_store,embeddings,reranker,query_rewriter,entity_extractor,rag_eval}.py` |
| 反馈闭环 / 口味画像 | `backend/app/services/feedback_loop.py`、`backend/app/repositories/feedback.py` |
| 营养报告 / 换菜 / 单位换算 | `backend/app/services/{nutrition,domain,unit_conversion}.py` |
| 异步任务 / Redis 运行时 / checkpoint | `backend/app/worker.py`、`services/{runtime,checkpoints}.py` |
| 前端 | `frontend/src/{router.ts,api.ts,stores/app.ts,views/*}` |
| 部署 | `docker-compose.yml`、`backend/Dockerfile`、`frontend/{Dockerfile,nginx.conf}` |

### 8.4 流程图渲染说明

本报告全部流程图采用 Mermaid 语法，结构统一为自上而下（`flowchart TD`）。渲染环境：GitHub / Typora / Obsidian 原生支持；VSCode 安装 `Markdown Preview Mermaid Support` 插件；导出图片可用 [mermaid.live](https://mermaid.live) 或 `mmdc` CLI。

---

> **报告结束** | SoloChef 个人项目分析报告 v9.0 | 编制日期 2026-08-09 | Phase 1/2/3 后端开发流程全部完成 | 全部技术结论可经附录 8.3 文件索引复核

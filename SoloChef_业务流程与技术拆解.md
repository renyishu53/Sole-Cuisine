# SoloChef 业务流程与技术拆解（按流程 × 技术栈）

> 文档目标：在「业务流程」维度重新拆解 SoloChef 后端，对每个流程明确 **用到了哪些技术栈**、以及 **该技术栈在代码中如何落地**（含关键文件与行号）。
> 代码基线：`backend/app/`（`SoloChefWorkflow` 11 节点 StateGraph + 14 张以 `user_id` 为核心的聚焦表）。
> 设计哲学贯穿全篇：**所有外部依赖（LLM / 向量库 / 图谱 / 重排 / VLM）都可选 + 优雅降级**，单点故障不阻断主链路。

---

## 0. 全局技术栈速查表

| 技术栈 | 角色 | 当前实现 | 是否可选 |
|---|---|---|---|
| FastAPI + Pydantic v2 | Web 框架 / 数据校验 | `app/main.py` + `app/api/router.py` | 必需 |
| SQLAlchemy 2.0 async + aiomysql | ORM / 数据库访问 | `app/db/session.py`、`app/repositories/` | 必需（MySQL；本地可 SQLite） |
| MySQL 8.0 / SQLite | 主数据库 | `config.py:16`（`mysql+aiomysql`）、14 张表 | 必需 |
| Alembic | 数据库迁移 | `migrations/0001`（初始）+ `0002`（删 6 遗留表） | 必需 |
| LangGraph `StateGraph` | 工作流编排 | `app/ai/workflow.py:133` | 必需 |
| LangChain `ChatOpenAI` | LLM 调用（OpenAI 兼容协议） | `app/ai/llm.py:95` 默认 `deepseek-chat` | LLM 可降级为 Demo |
| 结构化 JSON 输出 | 确定性结构化生成 | `.bind(response_format={"type":"json_object"})` | 必需（替代 function calling） |
| Milvus | 向量数据库（语义召回） | `app/services/milvus_store.py`、`knowledge.py:76` | RAG 可降级 |
| Neo4j | 知识图谱（实体/关系） | `app/services/graph_store.py` | RAG 可降级 |
| BGE-M3 / MiniLM | 文本嵌入模型 | `app/services/embeddings.py:59`（可选增强，缺权重降级 MiniLM） | 可选 |
| bge-reranker-v2-m3 | 二阶段精排 | `app/services/reranker.py`（可选，缺权重禁用） | 可选 |
| Celery + Redis | 后台异步任务 | `app/worker.py` | 入库/同步可同步兜底 |
| Qwen-VL（DashScope） | 多模态视觉 | `app/ai/vision.py:125` | 可选 |
| InMemorySaver | 断点续传 | `app/services/checkpoints.py` | 进程内 |
| Vue 3 + Vite + TS | 前端 SPA | `frontend/src/` | 必需（时间线未落地） |

---

## 1. 用户建档与画像管理

**流程**：新用户 → 填写性别/年龄/身高/体重/活动量/忌口/偏好/厨具 → 落库 `UserProfile` + `NutritionGoal` → 后续流程读取。

**用到的技术栈**：
- FastAPI 路由 + Pydantic v2 入参校验
- SQLAlchemy 2.0 async + aiomysql（MySQL）/ aiosqlite（本地）
- Repository 模式隔离持久化
- Alembic 迁移

**技术栈如何实现**：
- `app/db/session.py`：按方言区分连接池参数——SQLite 不传 `pool_size/max_overflow`（避免报错），MySQL 才传；`SessionFactory` 为异步上下文管理器。
- `app/models/__init__.py`：**14 张表全部以 `user_id` 为核心**（Phase 3 去家庭化，删 `families/memberships/profiles/invitations`）。`UserProfile` 存 `constraints`(忌口/过敏)、`preferences`、`kitchenware`、`prep_time_max`。
- `app/repositories/`：每个聚合一个 Repository，`get_unscoped` 等辅助查询。
- `app/api/router.py`：画像 CRUD 端点；`app/core/config.py:16` 默认 `mysql+aiomysql://solochef:...@localhost:3306/solochef`，本地零配置可设 `DATABASE_URL=sqlite+aiosqlite:///./solochef.db`。
- 迁移：`migrations/0001` 初始建表，`0002` 删除 6 张遗留表（calendar_events / plan_tasks / plan_budgets 等）。

---

## 2. 营养目标计算

**流程**：画像（性别/年龄/身高/体重/活动量）+ 目标取向（增肌/减脂/维持）→ TDEE + 宏量分配 → `NutritionGoal`。

**用到的技术栈**：
- 纯 Python 数学公式（Mifflin-St Jeor）
- Pydantic 模型 `NutritionGoal`

**技术栈如何实现**：
- `app/services/nutrition.py`：**无任何 LLM 调用**，纯函数计算。
  - TDEE = `10·体重(kg) + 6.25·身高(cm) − 5·年龄 + s`，`s=+5`(男)/`−161`(女)（Mifflin-St Jeor）。
  - 按 `goal_type` 分配宏量：增肌提高蛋白/总热量、减脂制造缺口、维持维持平衡。
- 结果注入规划工作流的 `WorkflowState.nutrition_targets`，供餐食智能体做约束式生成（非标签式）。

---

## 3. 周计划生成（核心 LangGraph 工作流）

**流程**：用户提交 `PlanningRequest{prompt, budget, user_id}` → 意图声明 → 双路并行检索 → 协调 → 三领域智能体并行生成 → 聚合 → 装配 → 校验 → 终稿。

**用到的技术栈**：
- LangGraph `StateGraph`（11 节点、并行分支、`operator.add` 累加）
- LangChain `ChatOpenAI`（OpenAI 兼容协议，默认 DeepSeek）
- 结构化 JSON 输出（`response_format=json_object`）
- Milvus 向量召回 + Neo4j 图谱召回（并行）
- BGE-M3/MiniLM 嵌入 + bge-reranker 二阶段精排
- 领域智能体引擎（LLM + 确定性兜底）
- 三级校验器（Verifier）
- InMemorySaver 检查点

**技术栈如何实现**：

1. **图构建** — `app/ai/workflow.py:133 _build_graph()`：
   - 11 节点：`intent → graph_retriever ‖ vector_retriever → coordinator → meal_agent ‖ shopping_agent ‖ budget_agent → domain_coordinator → planner → verifier → final_planner`。
   - 并行合并：`domain_results`/`specialist_outputs`/`trace` 用 `Annotated[list, operator.add]`（`workflow.py:80,83,85`），多节点 `return {"trace":[step]}` 自动拼接。
   - 编译：`builder.compile(checkpointer=self._checkpointer)`（`workflow.py:161`）。

2. **LLM 调用** — `app/ai/llm.py`：
   - `OpenAICompatiblePlanGenerator`（`llm.py:95`）：`ChatOpenAI(api_key, base_url=https://api.deepseek.com/v1, model=deepseek-chat)`；生成时 `bind(response_format={"type":"json_object"})`，把 `PlanDraft` 的 JSON Schema 注入 system prompt，流式 `astream` 收集后 `PlanDraft.model_validate(json.loads(...))`。
   - `DemoPlanGenerator`（`llm.py:66`）：`llm_provider="demo"`（默认）时返回确定性样例，保证零配置可跑通。
   - `build_plan_generator`（`llm.py:162`）：按 `real_llm_enabled`（provider≠demo 且配了 key）选择实现。

3. **RAG 检索** — `app/services/knowledge.py`：
   - `retrieve()`（`knowledge.py:247`）用 `asyncio.gather` **并行**跑向量召回与图谱召回。
   - 向量：`MilvusVectorStore.search`（Milvus 为当前唯一向量后端，`knowledge.py:76`）；若有 reranker，`candidate_k = top_k * rerank_candidate_multiplier(=3)`，再 `reranker.rerank` 精排回 `top_k`（`knowledge.py:214-226`）。
   - 图谱：`Neo4jGraphStore.search`，查询先经 `rewrite_query`（`query_rewriter.py`）改写。
   - 降级：任一不可达返回 `status="unavailable:..."`，不抛异常。

4. **嵌入** — `app/services/embeddings.py:59 create_embedding_backend()`：
   - `provider=auto` 先试 BGE-M3（`_try_bge_m3`，`local_files_only=True` 不隐式下载）；失败回退 `all-MiniLM-L6-v2`（384 维）。
   - `is_bge_m3` 标志驱动 Milvus 集合名后缀 `_bge_m3` 与向量维度（`milvus_store.py:58,64`：1024 vs 384）。

5. **领域智能体** — `app/ai/domain_agents.py StructuredDomainAgentEngine`：
   - 每智能体走「LLM JSON 生成 + 确定性兜底」双通道，LLM 失败自动回退不阻断。
   - `reconcile_budget()` 用等式钳制保证「分类限额 + 预留 == 周预算」，前端永不收硬冲突。

6. **校验** — `app/services/plan_validation.py`：
   - 三级自愈：自动修正（≤2 轮）→ 降级提示 → 硬冲突率>30% 人工接管。

---

## 4. 计划局部修改（Revise）

**流程**：用户自然语言修改要求 → LLM 解析为结构化操作 → 预览 diff → 确认派生新版本（保留完整对话历史）。

**用到的技术栈**：
- LLM 结构化 JSON 解析（7 类操作）
- Repository 局部改库（`derive_plan_with_modifications`）
- 对话持久化（ChatSession / ChatMessage）

**技术栈如何实现**：
- `app/services/plan_revise.py PlanReviseService`：
  - 自然语言 → LLM 解析为 7 种 `ReviseOperation`（`update_meal`/`replace_ingredient`/`relax_budget`/`relax_constraint` 等）。
  - **预览/提交分离**：`revise` 只生成预览存进 `ChatMessage.payload`，`confirm` 才调 `derive_plan_with_modifications` 落库（避免重复 LLM 调用）。
  - `_find_meal_index` 三级匹配：day+meal_type 精确 → 该天唯一餐 → 该天第一餐兜底。
- 对话复用：按 `[计划v{plan_id}]` 标题前缀复用会话，多轮修改保留完整历史（`app/repositories/conversations.py:get_message`）。
- 测试时强制 `plan_revise_service._llm_model=None` 走 demo 兜底（`tests/conftest.py`）。

---

## 5. 执行反馈闭环（长期记忆）

**流程**：用户标记执行偏差/口味 → 落库 + 回图谱 + 回向量 → 忌口自动纳入 + 口味画像 → 下一轮规划个性化收敛。

**用到的技术栈**：
- MySQL（`PlanFeedback` 表）
- Neo4j 图谱（`HAS_FEEDBACK` / `ABOUT` 关系 + `Preference` 节点）
- Milvus 滚动向量文档（`replace_document` 固定 doc_id，窗口 30 条）
- 规则引擎（连续负反馈 → 写忌口）

**技术栈如何实现**：
- `app/services/feedback_loop.py`：
  - `capture()`（`:219`）：写 `PlanFeedback` → 图谱 `sync` → 向量滚动文档（固定 `feedback-{user}-{type}` document_id，`replace_document` 覆盖，`window=30`）。
  - `replay()`（`:299`）：补偿未同步记录。
  - `apply_constraint_rules`（`:263`）：**连续 3 次负反馈自动写入 `UserProfile.constraints`**（忌口自动纳入）。
  - `taste_vector_from_tags`（`:123`）：生成 5 维口味向量，注入下一轮 meal 智能体（`workflow.run(taste_profile=...)` → `meal_agent_node` 读 `state["taste_profile"]`）。
- 闭环路径：执行 → 反馈 → 写回图谱/向量 → 下轮规划检索命中 → 个性化收敛。

---

## 6. 对话问答（RAG QA 流式）

**流程**：用户输入问题 → 取最近 8 轮历史 → RAG 检索 → LLM 流式生成 → SSE 推送 token。

**用到的技术栈**：
- SSE 流式推送
- RAG（Milvus + Neo4j）
- 短期记忆（8 轮滑动窗口）

**技术栈如何实现**：
- `app/services/conversation.py`：`ChatAssistant.answer` 由 `llm.py:254` 调用，把 `_extract_history`（`conversation.py:162`，取最近 8 条 `ChatMessage` 的 `user/assistant`）注入 prompt；RAG 命中片段拼接进上下文。
- 流式：`app/api/router.py` 暴露 SSE 端点，逐个 `yield` `event: step`（规划过程）与 `event: token`（生成内容）；前端 `api.ts:83` 的 SSE 解析器能解析 `step` 事件，但当前 UI 未消费（见流程 10）。

---

## 7. 视觉识别（VLM）

**流程**：用户上传图片（菜谱/营养标签/冰箱存货/餐食）→ 预处理 → Qwen-VL 理解 → 结构化 JSON → 回填计划或识别结果。

**用到的技术栈**：
- Qwen-VL（通义千问视觉，DashScope OpenAI 兼容接口）
- 结构化 JSON 输出
- 多场景 system prompt 路由

**技术栈如何实现**：
- `app/ai/vision.py:125 VisionService`：
  - 懒加载 `ChatOpenAI(api_key=vlm_api_key, base_url=https://dashscope.aliyuncs.com/compatible-mode/v1, model=qwen-vl-max)`（`vision.py:140`），`.bind(response_format={"type":"json_object"})` 保证结构化输出。
  - 5 个场景（`VisionScene`）：`RECIPE`（菜谱识别）、`NUTRITION_LABEL`（营养标签）、`FRIDGE`（冰箱/存货）、`MEAL`（餐食记录）、`GENERIC`；各场景独立 system prompt（`vision.py:43` 起）决定输出 JSON schema。
  - 图片预处理：长边超 2048px 缩放，控制 token 成本（`vision.py:86`）。
  - 可选：`vlm_enabled = bool(vlm_api_key)`（`config.py:108`），未配 key 时禁用，不阻断文本链路。

---

## 8. 知识库入库与图谱同步（后台任务）

**流程**：上传文档/文本 → 入队 Celery → 异步切块 → 写 Milvus + 抽实体写 Neo4j → 标记完成；定时清理历史任务。

**用到的技术栈**：
- Celery + Redis（broker + backend）
- 多队列隔离（knowledge / graph / maintenance）
- 死信与重试机制
- Milvus + Neo4j 写入

**技术栈如何实现**：
- `app/worker.py`：
  - `celery_app = Celery("solochef", broker=redis_url, backend=redis_url)`（`worker.py:11`，`config.py:21`）。
  - 3 个队列：`knowledge` / `graph` / `maintenance`，路由见 `task_routes`（`worker.py:34`）。
  - 任务：
    - `process_knowledge_text` / `process_knowledge_file`：调 `KnowledgeService.ingest_text/ingest_file` → `MilvusVectorStore.upsert_document` + `graph_store.sync_document_knowledge`（实体抽取 `entity_extractor.py`，默认本地正则，可选 LLM）。
    - `sync_member_graph`：把用户画像/菜谱/计划同步到 Neo4j（`worker.py:187`）。
    - `cleanup_old_jobs`：cron 每天 3:00 清理 30 天前终态任务（`worker.py:43`）。
  - 可靠性：`DeadLetterTask` 基类（重试 3 次 + 退避抖动，耗尽转死信写 `BackgroundJobRepository`）；`task_acks_late` + `task_reject_on_worker_lost` 防丢失；结果 1 小时过期（`result_expires=3600`）。

---

## 9. 断点续传（Resume）

**流程**：长规划任务中途中断 → 用 `thread_id` 恢复 state 与 trace → 续跑。

**用到的技术栈**：
- LangGraph 检查点（`BaseCheckpointSaver`）
- `InMemorySaver`（进程内）

**技术栈如何实现**：
- `app/services/checkpoints.py CheckpointRuntime`：封装 `InMemorySaver`（注释明确生产可换 `langgraph-checkpoint-redis`）。
- `app/ai/workflow.py`：`set_checkpointer`（`workflow.py:163`）+ `run(resume=True, run_id)`（`workflow.py:174`）+ `aget_state(config)`（`workflow.py:192`）用 `thread_id` 恢复 `trace` 与 state；编译时 `checkpointer=...` 注入。
- `app/api/router.py:1965`：已暴露恢复端点。
- **局限**：`InMemorySaver` 使断点仅在**同一进程生命周期**内有效，服务重启/多 worker 即失效，需换 Redis 才真正持久化。

---

## 10. 前端展示

**流程**：调用规划 API → 渲染餐食/采购/预算/来源 → （设计上）时间线 → 反馈 UI。

**用到的技术栈**：
- Vue 3 + Vite + TypeScript + SCSS
- API 客户端（含 SSE 解析）
- 类型契约（`AgentStep` / `PlanningResponse.trace`）

**技术栈如何实现**：
- `frontend/src/types.ts`：定义 `AgentStep{name,label,status,duration_ms,summary,output}`（`:28`）、`PlanningResponse.trace: AgentStep[]`（`:55`）、`ChatStreamEvent{event:'step';data:AgentStep}`（`:68`）——**数据契约已就绪**。
- `frontend/src/api.ts:83`：SSE 解析器会把 `event: step` 解析为 `onEvent({event,data})` 转发——**管道已铺**。
- `frontend/src/views/PlanDetailView.vue`：当前仅渲染 `plan.meals/shopping/budget/sources`，**未消费 `plan.trace`**。
- `frontend/src/assets/main.scss`：为时间线写了 `.trace-content`/`.duration`/`.trace-summary`/`.data-sources` 等样式——**孤儿 CSS，组件未接上**。
- 结论：规划过程时间线是「已设计、已铺管、未落地」的半成品；`trace` 后端完整可用，前端渲染缺失。

---

## 11. 跨流程的技术共性（工程亮点）

1. **结构化 JSON 替代 Function Calling**：全项目无 `bind_tools`/`@tool`，所有 LLM 产出均 `bind(response_format=json_object)` + Pydantic 校验 + 确定性兜底。收益=可控可降级；代价=放弃 ReAct 自主规划（有意为之）。
2. **可选增强统一模式**：LLM / BGE-M3 / reranker / VLM / Neo4j / Milvus 全部「探测可用性 → 失败降级 → 返回 status 字段」三件套，主链路零硬依赖。
3. **状态驱动编排**：`WorkflowState`（`workflow.py:50`）是单次 run 的「草稿纸」，`operator.add` 让并行节点零样板归并。
4. **长期记忆闭环**：流程 5 把执行反馈经 `capture()` 写回图谱/向量，下一轮（流程 3/6）检索命中后自动个性化——这是项目最扎实的 AI 能力。
5. **降级开关集中在 config**：`llm_provider`/`domain_agents_llm_enabled`/`entity_extraction_llm_enabled`/`vlm_enabled`/`rerank_enabled`/`rag_enabled` 让单测与离线环境可零配置跑通。

---

## 12. 已知限制（按流程）

| 流程 | 限制 |
|---|---|
| 3 周计划 | `intent` 节点是硬编码占位（非真实路由）；域智能体 LLM 默认关闭（`domain_agents_llm_enabled=False`） |
| 3 RAG | 向量库仅 Milvus；离线无 BGE-M3 权重时降级 MiniLM；reranker 离线禁用 |
| 5 长期记忆 | 口味向量/忌口闭环依赖反馈数据积累，冷启动弱 |
| 9 断点续传 | InMemorySaver，非持久化，多 worker/重启失效 |
| 10 前端 | 规划时间线未渲染；短期记忆无超长会话摘要压缩 |

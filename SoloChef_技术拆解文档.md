# SoloChef 项目技术拆解文档

> 阅读对象：需要理解整个代码库结构、业务流程与 Agent 子系统内部机制的开发者 / 评审者。
> 生成日期：2026-08-17。代码基准：`backend/app/` 当前主干。
> 重点章节：**第四章「Agent 技术拆解」**。

---

## 一、项目概览

### 1.1 产品定位

SoloChef 是一款面向**独居自炊人群**的「AI 膳食与采买规划师」。核心闭环是：

```
用户画像 → 营养目标(TDEE+宏量) → AI 生成周计划(三餐+采购清单) → 执行(打卡/核销)
      → 反馈闭环(回流图谱/向量) → 口味与忌口学习 → 下一轮规划
```

与早期「家庭综合事务规划师 HomePilot」相比，已**去家庭化**（删 families/memberships/calendar 等表），约束粒度收敛到单人 `UserProfile`。明确的差异化是：**营养目标驱动的约束式生成** + **执行反馈闭环**。

### 1.2 技术栈

| 层 | 选型 |
|---|---|
| 语言 / 运行时 | Python 3.12+ / 异步（asyncio + FastAPI） |
| Web 框架 | FastAPI，`/api/v1` 前缀 |
| 业务数据库 | **MySQL（主推）/ SQLite（本地零配置回退）** —— 已做方言适配 |
| 向量库 | Milvus（`solochef_knowledge` collection），Chroma 亦在 RAG 评测中验证过 |
| 图谱库 | Neo4j（bolt://localhost:7687） |
| 缓存 / 队列 | Redis（runtime_state）+ Celery（后台任务） |
| Agent 编排 | **LangGraph** `StateGraph`（11 节点，编译为可执行图） |
| LLM 接入 | `langchain_openai.ChatOpenAI`，OpenAI 兼容协议（DeepSeek / Qwen-VL 等） |
| 多模态 | Qwen-VL 图片理解（食材/菜品/营养标签/小票 OCR） |

### 1.3 数据层（14 张聚焦表）

全部位于 `app/models/identity.py`，以 `user_id` 为核心（去家庭化后不再有 family_id）：

`User` · `UserProfile` · `NutritionGoal` · `WeeklyPlan` · `PlanMealItem` · `PlanShoppingItem` · `RecipeRecord` · `PlanFeedback` · `AgentRunRecord` · `ChatSession` · `ChatMessage` · `ExpenseRecord` · `BackgroundJob` · `RefreshSession`

> 注：Phase 3 已清理 6 张遗留表（calendar_events / calendar_event_exceptions / plan_tasks / plan_budgets / task_completions / inventory_items）。`draft.tasks` 仍保留在 schema 中供前端使用，但不再持久化为独立任务表。

---

## 二、系统架构总览

### 2.1 分层结构

```
┌──────────────────────────────────────────────────────────────┐
│  前端 (Vue3, /frontend)  —  PlannerView / MealsView /         │
│                                ShoppingView / TasksView / 复盘页 │
└───────────────────────────┬──────────────────────────────────┘
                              │  HTTP / SSE (流式对话)
┌───────────────────────────▼──────────────────────────────────┐
│  app/main.py  FastAPI app (lifespan: 建表+知识库bootstrap)     │
│  app/api/router.py  87 个端点                                  │
│  app/api/dependencies.py  鉴权(CurrentContext/OwnerContext)    │
├──────────────────────────────────────────────────────────────┤
│  【服务层 services/*】                                          │
│   planning · plan_revise · feedback_loop · nutrition ·          │
│   knowledge(RAG) · conversation · substitution · weekly_report  │
│   domain · recipe · embeddings · reranker · graph_store · ...  │
├──────────────────────────────────────────────────────────────┤
│  【AI 层 app/ai/*】  ← 本文重点                               │
│   workflow(LangGraph 11节点) · domain_agents · llm · prompts ·  │
│   segmented_planner · evaluation · vision                      │
├──────────────────────────────────────────────────────────────┤
│  【仓储层 repositories/*】  planning/feedback/domain/identity/  │
│                              conversations                     │
├──────────────────────────────────────────────────────────────┤
│  【模型层 models/*】 SQLAlchemy ORM + Alembic 迁移             │
└───────────────────────────┬──────────────────────────────────┘
        ┌──────────┬──────────┬───────────┬───────────┐
        ▼          ▼          ▼           ▼           ▼
     MySQL      Redis     Milvus      Neo4j      LLM/VLM (外部 API)
```

### 2.2 外部依赖的降级哲学

贯穿全代码的工程准则是 **EAFP（Easier to Ask for Forgiveness than Permission）+ 优雅降级**：
- 任何外部底座（Milvus / Neo4j / LLM）不可达时，均捕获异常降级，绝不阻断主业务链路。
- 无真实 LLM 时，`llm_provider="demo"` 走 `DemoPlanGenerator` 返回预制周计划。
- 反馈回流失败只反映在 `plan_feedback.synced_to_*` 标记上，可由 `/feedback/resync` 补偿重放。

---

## 三、业务流程梳理

### 3.1 核心闭环（端到端）

```
① 建档        POST /profile (身高/体重/性别/活动量/目标类型/忌口/厨具/备餐时长)
② 营养目标    POST /profile/nutrition-goal  → Mifflin-St Jeor TDEE + ISSN 蛋白系数
③ 生成周计划  POST /plans/generate-weekly
                  └─→ PlanningService → SoloChefWorkflow(LangGraph 11节点) → PlanningResponse
④ 确认落库    POST /plans/{run_id}/confirm  → WeeklyPlan + 21 餐 + 采购清单
⑤ 局部修改    POST /plans/{plan_id}/revise → 预览 → POST /plans/{plan_id}/revise/{id}/confirm
⑥ 执行反馈    POST /meals/{id}/checkin（打卡/偏差）
              PATCH /shopping/{id}（采购核销 → 价格偏差反馈）
              POST /budget/expenses（支出 → 预算偏差反馈）
                  └─→ FeedbackLoopService.capture → 落库 + 回图谱 + 回向量 + 忌口自动纳入
⑦ 复盘        GET /reports/weekly（达成率/覆盖/口味雷达/建议）
⑧ 对话问答    POST /chat/sessions/{id}/messages[/stream] → ChatAssistant(RAG 问答，只读不改)
⑨ 多模态      POST /chat/vision → Qwen-VL 识别食材/菜品/标签/小票
```

### 3.2 关键 API 端点地图（按业务域）

| 域 | 端点（节选） |
|---|---|
| 规划 | `POST /plans/generate-weekly`、`POST /plans/{run_id}/confirm`、`GET /plans`、`GET /plans/active/overview` |
| 备餐修改 | `POST /plans/{plan_id}/revise`、`POST /plans/{plan_id}/revise/{revise_id}/confirm` |
| 餐食 | `GET/POST/PATCH/DELETE /meals`、`POST /meals/{id}/replace`、`POST /meals/{id}/checkin`、`GET /meals/taste-profile` |
| 采购 | `GET/POST /shopping`、`PATCH /shopping/{id}`、`/shopping/merge`、`/shopping/{id}/substitutions` |
| 营养 | `GET/POST /profile/nutrition-goal`、`GET /meals/nutrition`、`GET /meals/today/nutrition` |
| 反馈 | `GET /feedback`、`POST /feedback/resync`、`GET /feedback/taste-vector` |
| 对话/视觉 | `POST /chat/sessions/{id}/messages[/stream]`、`POST /chat/vision` |
| Agent | `GET /agents/prompts`、`GET /agents/evaluate`、`GET /agents/runs`、`POST /agents/runs/{id}/retry` |
| 知识库 | `POST /knowledge/documents/text`、`POST /knowledge/search`、`GET /admin/rag/eval` |

---

## 四、Agent 技术拆解（重点）

SoloChef 的「智能」由**两层 Agent 体系**构成：
1. **规划主链路 Agent** —— LangGraph 编排的 11 节点有状态工作流（`app/ai/workflow.py`）。
2. **领域智能体（Domain Agents）** —— 餐食 / 购物 / 预算三个 schema 约束的小模型调用（`app/ai/domain_agents.py`）。

外加若干辅助 Agent：对话助手（`ChatAssistant`）、视觉识别（`VisionService`）、评测（`evaluation`）。

### 4.1 规划主链路：LangGraph StateGraph（11 节点）

**文件**：`app/ai/workflow.py` —— `class SoloChefWorkflow`

#### 4.1.1 状态定义 `WorkflowState`

`WorkflowState(TypedDict, total=False)` 承载节点间传递的全部上下文：`request`、`user_constraints`、`user_preferences`、`prep_time_max`、`kitchenware`、`taste_profile`、`nutrition_targets`、`goal_type`、`graph_hits`、`vector_hits`、`context`、`draft`、`domain_bundle`、`trace` 等。

其中三个 `Annotated[list, operator.add]` 字段（`domain_results` / `specialist_outputs` / `trace`）用于**分支节点并发汇合时累加**，这是 LangGraph 多分支合并的标准写法。

#### 4.1.2 节点图

```
START
  │
  ▼
intent ──┬──▶ graph_retriever ──┐
         │                     │
         └──▶ vector_retriever ┘
                    │
                    ▼
              coordinator (融合图谱硬约束 + 向量语义上下文)
         ┌──────────┼──────────┐
         ▼          ▼          ▼
   meal_agent  shopping_agent  budget_agent   ← 三个领域智能体并行
         └──────────┼──────────┘
                    ▼
         domain_coordinator (校验+合并三个结构化结果 → DomainAgentBundle)
                    ▼
              planner (LLM 生成 PlanDraft)
                    ▼
              verifier (三级校验自愈)
                    ▼
              final_planner (汇总来源/校验)
                    ▼
                  END
```

编译方式（`_build_graph`）：
```python
builder = StateGraph(WorkflowState)
# 11 个 add_node(...)
builder.add_edge(START, "intent")
builder.add_edge("intent", "graph_retriever")
builder.add_edge("intent", "vector_retriever")
builder.add_edge(["graph_retriever", "vector_retriever"], "coordinator")
builder.add_edge("coordinator", "meal_agent")
builder.add_edge("coordinator", "shopping_agent")
builder.add_edge("coordinator", "budget_agent")
builder.add_edge(["meal_agent", "shopping_agent", "budget_agent"], "domain_coordinator")
# ... planner → verifier → final_planner → END
self._graph = builder.compile(checkpointer=self._checkpointer)
```

**关键设计点**：
- `graph_retriever` 与 `vector_retriever` 从 `intent` **分叉并行**；三个领域智能体从 `coordinator` **分叉并行**。并行节点通过 `operator.add` 累加其结果，由下游 `domain_coordinator` 合并。
- 检查点（checkpointer）默认 `InMemorySaver`（`app/services/checkpoints.py`），支持失败重放 `resume=True`（按 `thread_id` 取 `aget_state`）。**注意**：InMemorySaver 仅存活于进程内，重启丢失；生产可换 `langgraph-checkpoint-redis`。

#### 4.1.3 节点职责速查

| 节点 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `intent` | request | intent 字典 | 识别规划意图（目前硬编码 `weekly_plan`，requires=[meals,shopping,budget]） |
| `graph_retriever` | prompt, user_id | graph_hits, graph_status | 调 `knowledge.retrieve_graph` → Neo4j 成员关系/偏好/忌口 |
| `vector_retriever` | prompt, goal_type | vector_hits, vector_status | 调 `knowledge.retrieve_vector` → Milvus 语义召回（含可选 rerank） |
| `coordinator` | graph_hits, vector_hits | context 文本 | 把图谱关系 + 向量片段拼成融合上下文 |
| `meal_agent` / `shopping_agent` / `budget_agent` | request + 约束 | domain_results / specialist_outputs | 调 `StructuredDomainAgentEngine` |
| `domain_coordinator` | 三个领域结果 | domain_bundle | 校验+合并为 `DomainAgentBundle` |
| `planner` | context + specialist 建议 | draft(PlanDraft) | LLM 生成 21 餐 + 采购 + 预算；失败回退 Demo |
| `verifier` | draft + 约束 + 营养目标 | 校验后 draft + 冲突明细 | 三级自愈策略（见 4.5） |
| `final_planner` | 全部 | sources | 汇总数据来源（Neo4j / 向量 / 降级） |

每个节点都 `yield` 一条 `AgentStep`（name/label/status/duration_ms/summary/output）进入 `trace`，前端据此渲染**规划过程时间线**。

#### 4.1.4 运行与流式追踪

`run()` 用 `self._graph.astream(graph_input, stream_mode="values")` 逐步吐出 state，并通过 `on_step` 回调把增量 `trace` 推给上层（落 `AgentRunRecord` 检查点、SSE 前端）。`PlanningService.generate()` 负责在 `run()` 前后注入 `taste_profile / nutrition_targets / user_constraints / kitchenware / goal_type` 等，并持久化 `AgentRunRecord`。

---

### 4.2 规划生成器（Planning Agent 的核心 LLM）

**文件**：`app/ai/llm.py`

`SoloChefWorkflow` 持有的 `generator` 是 `PlanGenerator` 协议实例（`mode` + `generate()`）。`build_plan_generator()` 按配置三选一：

1. **`SegmentedPlanGenerator`**（当 `planner_segmented_enabled=True` 且真实 LLM 开启）—— 把单次调用拆成 meals→shopping→budget 三阶段（见 4.10）。
2. **`OpenAICompatiblePlanGenerator`**（默认真实 LLM）—— 单次调用，`ChatOpenAI.bind(response_format={"type":"json_object"})`，**强制 JSON 输出**，`temperature=0.2`，`max_tokens=4096`，支持 token 级流式（`token_sink` ContextVar 把 chunk 推给前端）。
3. **`DemoPlanGenerator`**（demo 模式）—— 返回 `demo_data` 中的预制 21 餐，计算演示用预算，不调任何模型。

`PlanDraft` 是规划产物 schema：`summary / meals(21×MealItem) / shopping / tasks / budget(BudgetSummary) / conflicts`。

**强约束校验**（防止垃圾输出）：
```python
def validate_weekly_meals(meals):
    if len(meals) != 21: raise  # 必须 7天 × 3餐
    slots = {(m.day, m.meal_type) for m in meals}
    # 必须覆盖全部 (周一~周日 × 早/午/晚)
```
`with_meal_types()` 把"早餐/午餐/晚餐"前缀从餐名解析回 `meal_type` 字段，兼容 demo 数据。

---

### 4.3 三大领域智能体：`StructuredDomainAgentEngine`

**文件**：`app/ai/domain_agents.py` —— 这是「Agent 中最小、最确定性、最可控」的部分，也是 SoloChef 工程化的精华。

#### 4.3.1 设计哲学

每个领域智能体（meal/shopping/budget）**优先用确定性规则生成 fallback**，仅在 `use_llm=True` 时把问题转成一次**最小 schema 约束的 JSON 调用**（`ChatOpenAI.bind(json_object)`，`max_tokens=900`，`temperature=0.1`）。LLM 失败自动退回确定性 fallback，**绝不抛出异常阻断主链路**。

#### 4.3.2 统一执行入口 `_generate()`

```python
async def _generate(self, schema, prompt: PromptVersion, request, fallback, extra_payload=None):
    if self._model is None:
        return fallback, f"deterministic:v{prompt.version}", ""
    user_prompt = (f"{prompt.instruction}\n只输出 JSON...\n"
                   f"Schema: {json.dumps(schema.model_json_schema())}\n"
                   f"Input: {json.dumps(payload)}")
    try:
        resp = await self._model.bind(response_format={"type":"json_object"}).ainvoke(
            [("system", prompt.system_message), ("user", user_prompt)])
        return schema.model_validate_json(content), f"llm:v{prompt.version}", ""
    except Exception as exc:
        return fallback, f"deterministic-fallback:v{prompt.version}", f"{type(exc).__name__}: ..."
```

**模式串编码提示词版本**（`deterministic:v1.3.0` / `llm:v1.3.0` / `deterministic-fallback:v1.3.0`），便于在评测/审计中追溯每个智能体当时用的是哪个提示词版本。

#### 4.3.3 三个智能体的产物 schema

| 智能体 | 产物（`app/schemas/domain.py`） | 关键字段 |
|---|---|---|
| `meal` | `MealAgentResult` | `strategy / constraints_applied / excluded_ingredients / preferred_tags / max_duration_minutes` |
| `shopping` | `ShoppingAgentResult` | `strategy / merge_keys / preferred_categories / purchase_windows` |
| `budget` | `BudgetAgentResult` | `limit / reserve / warning_threshold_percent / category_limits / self_check` |

#### 4.3.4 meal 智能体：约束注入最复杂

`meal()` 接收两层约束：
- **硬约束**：`user_constraints`（来自 `UserProfile.constraints` 忌口/过敏）+ `kitchenware`（厨具清单，进硬约束）。
- **软约束**：`user_preferences`（画像静态偏好）与 `taste_profile`（历史反馈聚合的口味画像，liked/disliked/rejected 标签）。

合并规则：`merged_tags = [t for t in (*liked, *preferences) if t not in disliked]`——**反馈学到的偏好排在画像静态偏好之前，负向标签一律剔除**。

**生活约束**：`prep_time_max` 直接决定 `max_duration_minutes`（优先于"快手"启发式，范围钳制 5–240 分钟）；`kitchenware` 进入 `excluded_ingredients` 之外独立的 `life_constraints`。

#### 4.3.5 budget 智能体：双层等式守恒

`budget()` 先生成确定性 fallback（预留 10%，分类限额 = 可分配额 × 固定比例：肉蛋奶 42% / 蔬菜 26% / 主食 17% / 其他补足）。无论 LLM 是否参与，最后都过一遍 `reconcile_budget()`：

```python
def reconcile_budget(result, budget_limit):
    # 若 分类限额之和 + 预留 != 周预算 → 按比例缩放分类限额，使等式严格成立
    # 填 result.self_check = BudgetSelfCheck(category_sum, total_check, expected, matched)
```

这保证**前端永不收到"分类限额超预算"的硬冲突**——这是把不可信的 LLM 输出"钳制"成业务可接受的确定性结果。

#### 4.3.6 domain_coordinator 的合并

三个结果经 `domain_coordinator` 合并为 `DomainAgentBundle`：
```python
merged_constraints = [
  *meal.constraints_applied,
  f"单餐时长不超过 {meal.max_duration_minutes} 分钟",
  f"预算预留 {budget.reserve:.2f} 元",
  shopping.strategy,
]
```
该 bundle 被注入 `planner` 的 prompt（`specialist_outputs` + `domain_context`）成为 LLM 生成时的「专家约束」，同时完整回传前端展示"专家给了什么建议"。

---

### 4.4 提示词版本注册表（"提示词即代码"）

**文件**：`app/ai/prompts.py`

三个领域智能体的系统提示与指令集中声明在 `_REGISTRY` 字典，**每个提示词带语义化版本号 + changelog + 发布日期**。例如 meal 智能体历经 4 个版本：
- `1.0.0` 初始：按成员硬约束过滤
- `1.1.0` 强化过敏信息必须来自画像
- `1.2.0` 接入 `taste_profile`（口味偏好学习）
- `1.3.0` 接入 `lifestyle`（备餐时长/厨具）

`StructuredDomainAgentEngine` 通过 `get_active("meal")` 读取**最新**版本；`list_versions()` 可回看历史，实现 A/B 对比与回滚。`GET /agents/prompts` 把整套注册表暴露给前端。

---

### 4.5 Verifier 三级校验自愈策略（阶段3核心）

**文件**：`app/ai/plan_validation.py` + `workflow._verifier_node`

verifier 是规划主链路的质量闸门，实现**三级自愈**：

- **第 1 级 自动修正（最多 2 轮）**：仅处理**软冲突**（重复→缺天→预算），从内置候选菜池（`_REPLACEMENT_MEALS`，10 道家常菜，已做忌口过滤）替换。每轮修正后重跑 `detect_conflicts`，捕捉"修了重复又超预算"的震荡。
- **第 2 级 降级提示**：**硬冲突**（忌口/分类限额）不自动改用户约束，而是生成 2–3 个 `ConflictOption`（换菜/放宽预算/换食材）供前端选择。
- **第 3 级 人工接管**：硬冲突率（`硬冲突数 / 总餐数`）> 30% 时 `needs_manual_review=True` 并给出放宽建议提示。

`detect_conflicts` 输出结构化 `PlanConflict(dimensions, level, message, item, options)`——维度含 `allergy/budget/coverage/duplicate/category_limit/nutrition`。计算忌口违禁词用 `compute_forbidden_terms()`（支持"不吃辣"→辣椒/辣酱/麻辣 别名展开，避免误伤"不辣"标签）。

**预算钳制**：verifier 还把 `draft.budget.estimated` 钳制到 `request.budget` 上限，并填 `saved / usage_percent`。

---

### 4.6 反馈闭环（Feedback Loop）—— 让 Agent「越用越懂你」

**文件**：`app/services/feedback_loop.py` —— `class FeedbackLoopService`

**唯一入口** `capture(session, signal: FeedbackSignal)`：
1. **落库**：写 `plan_feedback` 偏差表（主观反馈 + 客观偏差 planned/actual）。
2. **回图谱**：`(:User)-[:HAS_FEEDBACK]->(:FeedbackSignal)-[:ABOUT]->(:KnowledgeEntity)`，正/负信号额外连 `Preference` 节点（`graph_store.sync_feedback_signal`）。
3. **回向量库**：按反馈类型维护**固定 document_id** 的滚动文档（`feedback-{user_id}-{type}`），只保留最近 30 条（`_VECTOR_WINDOW`），下一轮 RAG 即可召回"上次这道菜太辣"。

**确定性情感判定**：`classify_sentiment()` 评分优先，否则用中文正/负向词典计票（`_POSITIVE_PHRASES` / `_NEGATIVE_PHRASES`），无需 LLM 即可判定。

**口味画像五维向量**：`taste_vector_from_tags()` 把 liked/disliked 标签映射为「辣/清淡/甜/咸/酸」五维（-1..1）。用同义词集合精确匹配，避免"甜"误入"酸甜"→酸。

**忌口自动纳入（阶段5）**：连续 3 次负向餐食反馈的食材标签，自动追加进 `UserProfile.constraints`（`apply_constraint_rules`，从最新往回数连续负向，被正/中性打断即清零）。

**补偿重放**：`replay()` 把因 Neo4j/向量库不可达而未回流的反馈重推一次；`/feedback/resync` 端点批量重放。

> 反馈画像如何回到规划：PlanningService 在 `generate()` 时调 `FeedbackRepository.taste_profile()` 注入 `taste_profile` 给 meal 智能体——这是"反馈 → 记忆 → 下一轮规划"闭环的桥接点。

---

### 4.7 备餐局部修改（Plan Revise Agent）

**文件**：`app/services/plan_revise.py` —— `class PlanReviseService`

把"自然语言修改要求"经 LLM 解析为结构化 `ReviseOperation`（**7 种**：`replace_meal / remove_meal / add_meal / exclude_ingredient / update_budget / skip_day / adjust_macro_target`），再确定性执行业务修改。

**关键设计（预览/提交分离）**：
1. `generate_preview()`：LLM 解析 + 业务执行（内存中，不落库）+ 计算 before/after `PlanSnapshot` 与 `PlanDiff` 营养/预算 delta → 返回预览，并把结果存进 `ChatMessage.payload`（JSON），**不持久化计划本身**。
2. 前端确认后 `confirm` → `PlanningRepository.derive_plan_with_modifications` 派生**新版本**（`parent_plan_id` 指向旧版）。

`adjust_macro_target` 仅记录意图（不直接改 `NutritionGoal`，需用户在营养目标页确认后重算），避免服务内职责越界。

**Demo 兜底**：无真实 LLM 时 `_parse_demo_operation()` 用正则关键词（`换成/不要/预算降/周末不做/蛋白质/加一餐`）映射 7 种操作，保证测试可跑。

**餐食定位 `_find_meal_index`**：三级匹配——day+meal_type 精确 → 该天唯一餐 → 该天第一餐兜底。

---

### 4.8 对话助手（ChatAssistant）

**文件**：`app/ai/llm.py` —— `class ChatAssistant`

与规划生成器区分开：**不绑定 JSON**，自然语言流式输出，`temperature=0.6`，走 RAG 问答。组装只读上下文（用户画像 + 营养目标 + 当前计划摘要）+ RAG 片段 + 多轮历史（最近 8 轮），注入系统提示"你是 SoloChef 营养助手，不要生成完整周计划"。`conversation_service.stream_turn` 用 **SSE** 推送 token 事件（message/thinking/token/complete/cancelled/error），支持中途取消（`runtime_state.is_cancelled`）。**对话模式只读不改业务数据**。

---

### 4.9 多模态视觉识别（VisionService）

**文件**：`app/ai/vision.py`

基于 Qwen-VL，与文本 LLM 链路解耦（`vlm_*` 独立配置）。`preprocess_image()` 把图片**长边压缩到 2048px + JPEG quality=85** 控制 token 成本。5 种场景（AUTO/INGREDIENT/DISH/NUTRITION_LABEL/RECEIPT）各有独立 system prompt，均 `bind(response_format=json_object)`，返回 `VisionResult(summary/items/calories/raw_text)`。

---

### 4.10 规划器进阶：分段生成（P3 可选增强）

**文件**：`app/ai/segmented_planner.py` —— `class SegmentedPlanGenerator`

把单次 LLM 调用（meals+shopping+budget 一体）拆成 3 个聚焦小 prompt 阶段：
1. **meals**：基于需求 + RAG 上下文生成 21 餐
2. **shopping**：基于上一步 meals 的 ingredients 推导采购清单（减少幻觉）
3. **budget**：基于 shopping 计算预算分配

每阶段独立 JSON 解析与校验，任一阶段失败自动回退到 `OpenAICompatiblePlanGenerator` 单次模式（向后兼容）。默认**关闭**（`planner_segmented_enabled=False`），当前 Verifier 兜底已够用。实现 `PlanGenerator` 协议，对 `SoloChefWorkflow` 完全透明。

---

### 4.11 领域智能体评测（AgentEval）

**文件**：`app/ai/evaluation.py`

对已完成计划离线评分（不调 LLM，可重复）：
- **meal**（40%权重）：硬约束满足率（忌口命中）+ 时长上限遵守率
- **shopping**（30%）：餐食食材在采购清单的覆盖率
- **budget**（30%）：估算金额贴近限额且不超支

`evaluate_plan()` 返回 `AgentEvaluation(overall_score, scores, details, issues, prompt_versions)`。忌口约束同样来自单人 `UserProfile.constraints`（去家庭化后的唯一数据源）。`GET /agents/evaluate` 暴露给前端"智能体评测"面板。

---

### 4.12 降级与可恢复性汇总

| 机制 | 触发 | 行为 |
|---|---|---|
| Demo 规划器 | `llm_provider=demo` 或无 API Key | 返回预制周计划 |
| 规划超时/失败回退 | `ai_fallback_enabled=True` 且 `LLMGenerationError/TimeoutError` | `planner` 节点改用 `DemoPlanGenerator` |
| 领域智能体回退 | LLM 调用异常 | 返回确定性 fallback（模式串记 `deterministic-fallback`） |
| 分段生成回退 | 任阶段失败 | 回退单次生成 |
| 预算等式钳制 | LLM 输出不自洽 | `reconcile_budget` 强制分类和+预留=预算 |
| 检查点重放 | Agent Run 失败 | `planning.resume()` + `InMemorySaver` |
| 反馈补偿 | Neo4j/向量不可达 | 记录 `synced_to_*`=False，`/feedback/resync` 重放 |
| 知识库/bootstrap 失败 | Milvus/Neo4j 不可达 | 启动降级跳过，不阻断 API |

---

## 五、关键设计模式与工程亮点

1. **确定性优先的 LLM 治理**：所有 LLM 输出都过 Pydantic schema 校验 + 确定性兜底 + 等式/范围钳制，把"不可信的生成"约束成"业务可接受的产物"。`reconcile_budget`、`validate_weekly_meals`、`reconcile` 是典型代表。
2. **提示词版本注册表（prompts-as-code）**：把 prompt 当成代码管理，支持审计、A/B、回滚。
3. **Agent 模式串可追溯**：`deterministic/llm/fallback : v版本` 让每次生成的每个智能体都可被审计。
4. **LangGraph 分叉-汇合**：retriever 与领域智能体并行分支，用 `operator.add` 累加结果，下游合并——清晰的有状态编排。
5. **反馈闭环的双写（MySQL + Neo4j + 向量）**：执行结果回流到图谱与向量库，使下一轮 RAG/约束真正"学到"用户偏好，且任一底座不可用都不阻断。
6. **预览/提交分离的修改机制**：备餐修改先算 diff 预览存对话历史，确认才派生新版本，避免反复 LLM 调用与误改。
7. **方言无关的设计**：`db/session.py` 按 SQLite 区分连接池参数；`checkpoints` 用 InMemorySaver 不绑方言；测试用 SQLite 内存库直跑。

---

## 六、已知限制与待深化方向

| 项 | 状态 | 说明 |
|---|---|---|
| 检查点持久化 | 进程内 | InMemorySaver 重启即丢；生产建议换 Redis checkpointer |
| 领域智能体真实 LLM | 默认关闭 | `domain_agents_llm_enabled=False`，当前走确定性规则（注释称"避免与主规划器串联多次外部等待"） |
| 分段生成 | 默认关闭 | P3 可选增强，Verifier 兜底已够 |
| 图谱实体抽取 LLM | 默认关闭 | `entity_extraction_llm_enabled=False`，用本地正则抽取 |
| reranker / BGE-M3 嵌入 | 可选 | 默认 rerank_enabled=True 但本地无权重时优雅降级 `rerank_status=disabled` |
| 剩余可选深化 | G07/G08/G11/G14/G15 | 食材替换营养联动 / 购物替代图谱化 / 前端复盘页 / MySQL 集成测试 / 前端测试 |
| 工作流残留 | 已知 | `members`/`CalendarEvent` 在 knowledge/graph_store/conversation 签名中仍作可选空参（更广残留面未动） |

---

## 七、一句话总结

SoloChef 的 Agent 子系统是一套**"约束式生成 + 确定性兜底 + 反馈学习"**的工程化实现：用 LangGraph 把 RAG 检索、三大领域智能体、LLM 规划器、三级校验 Verifier 编排成 11 节点有状态工作流；每个 LLM 调用都被 Pydantic schema、业务等式（预算守恒）、提示词版本与确定性 fallback 层层约束；执行反馈通过双写图谱与向量库回流成下一轮规划的口味/忌口记忆。其最大的工程价值不在于"用了多少 LLM"，而在于**把 LLM 不可控的输出驯服成可审计、可降级、可复现的业务产物**。

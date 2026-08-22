# SoloChef — 面试与技术介绍文档

> 用途：面向面试官 / 技术人员系统介绍 SoloChef 项目
> 内容：项目概览 + 业务流程 + 技术架构 + 各模块技术拆解 + Agent 技术深度 + 工程化与交付 + 踩坑与面试问答
> 项目定位：面向**独居自炊人群**的 AI 膳食规划与采买助手（增肌 / 减脂 / 健康维护三类目标）

---

## 一、项目概览

### 1.1 是什么

SoloChef 是一个面向独居自炊人群的 AI 膳食规划助手。它把独居做饭最头疼的四件事串成一条智能闭环：**算营养目标 → 生成一周三餐 → 列出采购清单 → 根据反馈学你的口味**。

### 1.2 核心差异化

区别于市面上"标签式推荐"的菜谱 App，SoloChef 的核心是**营养目标驱动的约束式生成 + 执行反馈闭环**：

- 不是"你选了减脂标签，就推减脂菜"——而是先算出你每天该吃多少大卡、蛋白碳水脂肪各多少克，再用这些**硬约束**驱动 AI 生成
- 不是"生成完就结束"——用户打卡反馈后，系统**自动学习**口味画像，下一轮规划据此调整

一句话技术灵魂：**"约束式生成 + 确定性兜底 + 反馈学习"**——LLM 只负责"创意"，所有"正确性"交给确定性代码把关。

### 1.3 技术栈总览

| 层 | 技术 |
|---|---|
| 前端 | Vue 3.5 + TypeScript 5.8 + Vite 7 + Pinia 3 + Vue Router 4 + Axios + ECharts 6 + Element Plus 2.14 |
| 后端 | FastAPI（Python 3.12）+ SQLAlchemy 2.0（异步）+ Pydantic v2 |
| AI 编排 | LangGraph（StateGraph）+ OpenAI 兼容 LLM |
| 主库 | MySQL 8.0（14 张以 user_id 为核心的表） |
| 缓存/运行时 | Redis 7 |
| 知识图谱 | Neo4j 5（管硬约束：忌口、食材替代关系） |
| 向量库 | Milvus 2.4（生产）/ Chroma（本地零依赖适配） |
| 嵌入模型 | BGE-M3（BAAI/bge-m3，归一化向量） |
| 重排模型 | bge-reranker-v2-m3（BAAI/bge-reranker-v2-m3，二阶段精排） |
| 异步任务 | Celery（worker，concurrency=2） |
| 编排 | Docker Compose（8 服务：backend/worker/frontend/mysql/redis/neo4j/etcd/minio/milvus） |

### 1.4 交付状态

**可演示 / 可联调，不建议直接作为生产版本交付**：

- 前端可交付：`vue-tsc` 类型检查零错误 + 18 个 Vitest 用例通过 + Vite 生产构建通过
- 后端核心可运行：编译通过、可导入；但全量 pytest 仍有部分失败（计划校验断言、分段生成测试触发真实 LLM 401、Windows 临时目录权限）
- 已知局限：checkpoint 用 InMemorySaver（重启丢失）、密钥需走 Secret 管理、向量同步可引入 MQ

---

## 二、业务流程（六阶段闭环）

SoloChef 的业务是一个**"建档 → 生成 → 执行 → 反馈 → 学习 → 触发下轮"**的完整闭环。下面按真实用户操作顺序逐阶段拆解。

### 阶段 1：入驻建档

**用户做什么**：注册（手机号+短信验证码+密码）→ 填身体数据（身高/体重/年龄/性别/活动量/目标类型）→ 填饮食约束（忌口/偏好/预算）→ 填生活约束（厨艺/厨具/最长备餐时间）→ 点"计算营养目标"。

**系统发生什么**：
- 注册建 `User`，发 JWT + Refresh
- 填画像 Lazy 创建 `UserProfile`（永不 404）
- 计算营养目标：Mifflin-St Jeor 算 BMR → × PAL 算 TDEE → × 目标系数算目标热量 → 三维表算宏量区间 → 落 `NutritionGoal`
- 建档完成判定：`nutrition_goals` 表存在该行即为完成，前端据此放开"生成周计划"入口

**关键设计**：营养计算是**纯确定性函数、绝不调 LLM**（医学公式不交给可能幻觉的模型）；目标用**区间**而非单值，为后续 Verifier 的 [90%,110%] 达成率判定埋伏笔。

### 阶段 2：营养目标求解

**用户做什么**：看到一组数字（每天大卡、蛋白/碳水/脂肪克数）+ 直观解释（"相当于两块鸡胸肉"）。

**系统发生什么**（`services/nutrition.py` `compute_nutrition_goal`）：
- BMR = 10×体重 + 6.25×身高 − 5×年龄 + 性别常数（男+5/女−161）
- TDEE = BMR × PAL（sedentary 1.40 / light 1.50 / moderate 1.75 / active 2.00，锚定中国 DRIs 2023）
- 目标热量 = TDEE × 系数（增肌 1.10 / 减脂 0.85 / 维持 1.0），给 ±7% 区间
- 蛋白质：体重 × g/kg 系数（三维表 `目标类型 × 活动量`，如减脂+active = 1.4~1.8）
- 脂肪/碳水按 AMDR 供能比补足
- 安全钳制：蛋白供能比 >30% 强制下调；TDEE 钳制 [1000, 5000]

### 阶段 3：AI 周计划生成（核心）

**用户做什么**：点"生成周计划"，看到计划**一步步被组装**（先算约束→并行检索→三领域并行生成→校验），最终得到 21 餐 + 采购清单 + 预算的"预览态"计划，确认后正式生效。

**系统发生什么**：主规划工作流（11 节点 LangGraph StateGraph）执行，详见第四章和第五章。

### 阶段 4：备餐规划局部修改

**用户做什么**：看完周计划，用自然语言提修改要求（"周三晚餐换鸡胸肉""牛奶别买了""预算降到 300"），看到修改预览（哪些餐变、购物怎么联动、预算差多少），确认后派生新版本。

**系统发生什么**：修改工作流（条件路由图）执行，详见第六章。

### 阶段 5：购物执行与打卡反馈

**用户做什么**：去采购，勾选"已买"、标记替代；一周里在任务页/餐食页/购物页反馈（这餐好不好吃、临时换了菜、实际花了多少钱）。

**系统发生什么**：
- 购物修改 409 版本保护（基于过期版本修改返回 Conflict）
- 6 类反馈信号规范化为 `FeedbackSignal`（带 deviation 实际-计划、narrative 自然语言摘要）
- 落 MySQL（主链路唯一不可降级步骤）

### 阶段 6：学习复盘与触发下轮

**用户做什么**：在"口味画像"面板看到系统学到了什么（喜欢/拒绝的标签与菜、最近原话）；连续三次说"太辣"后，下次生成就自动避开辣味。

**系统发生什么**：FeedbackLoopService 执行三库回流 + 忌口自动纳入 + 口味画像聚合 + 下一轮注入，详见第七章。

---

## 三、技术架构总览

### 3.1 分层架构

```
┌─────────────────────────────────────────────┐
│  前端  Vue3 + TS + Vite（10 个视图）          │
│  PlannerView / MealsView / ShoppingView ...  │
└──────────────────┬──────────────────────────┘
                   │ HTTP / SSE
┌──────────────────▼──────────────────────────┐
│  API 层  FastAPI（87 端点，按功能域分组）      │
│  auth_router + router（认证12 + 业务75）       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  服务层  PlanningService / KnowledgeService  │
│  FeedbackLoopService / PlanReviseService      │
│  ConversationService / NutritionService ...   │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  AI 层  LangGraph 工作流 + 三领域智能体        │
│  SoloChefWorkflow（11节点）+ PlanRevisionWorkflow│
│  StructuredDomainAgentEngine + Verifier      │
└──────┬──────────┬──────────┬────────────────┘
       │          │          │
   ┌───▼───┐  ┌───▼───┐  ┌──▼──────┐
   │ MySQL │  │Neo4j  │  │Milvus   │  ← Redis 缓存
   │14表    │  │图谱   │  │向量库    │
   └───────┘  └───────┘  └─────────┘
```

### 3.2 Docker Compose 编排

8 个服务：**backend**（FastAPI）、**worker**（Celery，concurrency=2）、**frontend**（Nginx 托管 Vue 产物 + 反向代理）、**mysql:8.0**、**redis:7-alpine**、**neo4j:5-community**、**etcd + minio + milvus:2.4**（Milvus 依赖 etcd 做协调、minio 做对象存储）。

**启动顺序靠 healthcheck 控制**：MySQL/Redis/Neo4j 健康后 backend 和 worker 才启动，避免连不上库陷入崩溃重启循环。前端多阶段构建，node 只负责编译，最终镜像只有 Nginx 加静态文件。

### 3.3 数据模型

14 张以 `user_id` 为核心的聚焦表（去家庭化后，所有业务表 `family_id → user_id`）。核心表：
- 身份：`users` / `user_profiles` / `nutrition_goals`
- 计划：`plans` / `plan_meals` / `plan_shopping_items`
- 反馈：`plan_feedback`（偏差表）
- 对话：`chat_sessions` / `chat_messages`
- 知识：`recipes` / `documents` / `agent_runs`

---

## 四、各模块技术拆解

### 4.1 认证模块

- **JWT 双令牌**：Access（短时效）+ Refresh（长时效）。拆两个的原因：只用短 token 用户频繁被踢出，只用长 token 泄露后攻击窗口太长
- **token_version 登出即失效**：改密码/全设备登出时 token_version+1，旧 token 因版本不匹配直接失效；refresh 单次使用、发新撤旧防重放
- **短信验证码**：调短信 SDK 下发，存 Redis 设 5 分钟 TTL，发送冷却也靠 Redis TTL 自动过期
- **密码**：bcrypt 加盐哈希
- **路由守卫**：Vue Router meta 标记需登录页面，未登录跳登录页并记住目标路径

### 4.2 营养计算模块

纯确定性函数（`services/nutrition.py`），详见阶段 2。要点：Mifflin-St Jeor 公式、DRIs 2023 PAL、三维蛋白系数表、安全钳制、目标用区间。

### 4.3 计划生成模块

由 `PlanningService` 编排，注入 5 类上下文给工作流：
- `taste_profile`（口味画像，来自反馈聚合）
- `nutrition_targets`（营养目标）
- `user_constraints`（忌口，来自 UserProfile.constraints）
- `goal_type`（目标类型，作向量元数据过滤）
- `lifestyle`（厨艺/厨具/备餐时间，驱动 meal agent 的 max_duration 与食材可行性）

AgentRun 状态机持久化 + 异常分类落库。

### 4.4 计划校验模块（Verifier）

后置确定性校验器，用 [90%, 110%] 区间判定营养达成率。三级自愈：
1. 自动修正（`apply_auto_fix`，最多 2 轮，有界修复防死循环）
2. 降级提示（修不动写偏差说明进计划）
3. 人工接管（硬冲突率 >30% 置 needs_manual_review）

### 4.5 计划修改模块（PlanRevise）

预览/确认分离协议：
- `revise`：LLM 解析自然语言 → 8 种 ReviseOperation → 修改工作流执行 → before/after diff 存 ChatMessage.payload，**不落库**
- `confirm`：用户确认 → `derive_plan_with_modifications` 派生新版本才落库

8 种操作：replace_meal / remove_meal / add_meal / exclude_ingredient / update_budget / skip_day / adjust_macro_target / adjust_shopping

### 4.6 购物执行模块

- 409 版本保护：基于过期版本修改返回 Conflict
- 6 类反馈信号：task_completion / meal_replacement / meal_rating / meal_checkin / shopping_verification / expense_record
- `FeedbackSignal` 自带 deviation（实际-计划）和 narrative()（自然语言摘要供向量检索）

### 4.7 反馈闭环模块（FeedbackLoopService）

核心是"执行反馈 → 三库回流 → 聚合画像 → 注入下一轮"：
- 回图谱（Neo4j）：建 `User-[:HAS_FEEDBACK]->FeedbackSignal-[:ABOUT]->实体`，正负向额外连 Preference
- 回向量库（滚动文档）：固定 document_id，内容是最近 30 条 narrative 快照（避免每周 21 餐反馈刷屏）
- 忌口自动纳入：负向餐食反馈连续 3 次阈值，自动写进 UserProfile.constraints（单次可能偶然，连续 3 次才稳定成硬约束）
- 口味画像聚合：从 plan_feedback 实时重算 TasteProfile，正负矛盾用净票去矛盾

**最精髓设计——双路生效**：taste_profile 同时作用于 LLM 路径（写进提示词）和确定性回退路径（直接进 MealAgentResult 计算，excluded_ingredients = disliked + rejected + 硬约束）。没有 LLM 时反馈依然被学习。

### 4.8 对话服务模块（ConversationService）

- SSE 流式（`text/event-stream`）：前端 EventSource → FastAPI StreamingResponse → LLM astream 逐 token
- 多轮记忆：ChatSession/ChatMessage 持久化，取最近 8 条做上下文
- 只读上下文注入：用户画像 + 营养目标 + 当前周计划摘要
- RAG 检索：对话时也调 RAG（向量 top3 + 图谱 top2），失败返回空不阻断
- SSE 事件 ID + 断线重连补齐：每条消息带递增 event_id 存 Redis，EventSource 自带重连

### 4.9 报告模块

`WeeklyReportView` + ECharts 6 做营养达标可视化（分布饼图、趋势柱状图）。

---

## 五、Agent 技术深度（重点）

### 5.1 约束式三段式生成

SoloChef 的 Agent 不是"给 LLM 一个 prompt 让它自由发挥"，而是三段式：

| 段 | 职责 | 实现 |
|---|---|---|
| **前置约束** | 解析用户约束 + RAG 检索注入 | constraint_parser 节点 + 双路检索 |
| **中置生成** | schema 绑定的结构化输出 | 三领域智能体（temperature=0.1, max_tokens=900） |
| **后置校验** | 确定性代码把关正确性 | Verifier（[90%,110%] + 三级自愈） |

**为什么这样做**：LLM 负责"创意"（搭配什么菜），确定性代码负责"正确性"（营养是否达标、预算是否守恒、忌口是否避开）。标签式生成有 3 个致命问题：约束不可验证、失败不可自愈、输出不可审计——约束式三段式逐一解决。

### 5.2 三领域智能体

膳食/采购/预算三个智能体，由 `StructuredDomainAgentEngine` 统一驱动：

- **schema 绑定 JSON 输出**：强制模型输出符合定义的字段结构，不依赖自由文本解析
- **参数**：temperature=0.1（低随机性）、max_tokens=900（控成本）、max_retries=1
- **无数据库写权限**：只产出"建议"（MealAgentResult 等），AI 永远不能直接改库——边界安全设计
- **use_llm 标志**：只有 `domain_agents_llm_enabled && mode != "demo"` 才走真实 LLM，否则确定性回退

**为什么拆三个**：约束边界不同（膳食看营养、采购看库存/替代、预算看价格）、降级粒度不同（一个失败不影响另两个）、可独立演化。

### 5.3 确定性兜底（EAFP 全链路降级）

每个智能体都有一条不依赖 LLM 的回退路径。整个系统的外部依赖（Neo4j/向量库/Redis/LLM/VLM）任一异常只降级不抛错：

| 依赖 | 失败时降级 |
|---|---|
| LLM | 走确定性回退路径（规则+食材营养库） |
| Neo4j | 检索返回空 + synced_to_graph=False |
| 向量库 | 检索返回空 + 状态标记 |
| Reranker | rerank_status=disabled，截断 top_k |
| BGE-M3 权重 | 回退 all-MiniLM-L6-v2（384维） |
| Redis | 进程内内存降级 |

**没有 LLM 时系统还能用**：每个智能体有确定性回退 + taste_profile 双路生效，离线/欠费仍给出可用计划且持续学习。

### 5.4 预算守恒 reconcile_budget

第二层确定性防御：代码级保证"采购总额 = 预算上限"等式成立。即使 LLM 算错，这一层强制对齐。

### 5.5 提示词版本注册表（prompts-as-code）

提示词带语义化版本（v1.0.0 → v1.3.0），支持审计、回滚、A/B 测试。模式串：`deterministic:v1.3.0` / `llm:v1.3.0` / `deterministic-fallback:v1.3.0`。

### 5.6 token_sink ContextVar

`llm.py` 的 `token_sink` ContextVar 流式 token 穿透——工作流层能感知 LLM 实际产出的 token，用于成本审计与流式回调，而不破坏 LLM 调用的封装。

### 5.7 Verifier 三级自愈为什么是 2 轮

自动修正最多 2 轮——有界修复而非无限重试。2 轮是平衡点：给模型纠错机会，又防止陷入死循环；修不动就走降级提示或人工接管。

---

## 六、两个工作流对比

项目有两套 LangGraph StateGraph 工作流，服务两种本质不同的计算。

### 6.1 主规划工作流 `SoloChefWorkflow`（workflow.py）

**定位**：从零生成完整周计划（高成本、并行、可断点续传）

**11 节点 + 两处并行**：
```
START
  → constraint_parser（解析约束）
  → 并行①: graph_retriever ∥ vector_retriever（双路检索）
  → 汇聚: coordinator（组装领域上下文）
  → 并行②: meal_agent ∥ shopping_agent ∥ budget_agent（三领域并行）
  → 汇聚: domain_coordinator（归约三智能体产出）
  → planner（组装 PlanDraft）
  → verifier（确定性校验 + 三级自愈）
  → final_planner（组装 PlanningResponse）
  → END
```

**关键设计**：
- 三个 `Annotated[list, operator.add]` 归约器（domain_results / specialist_outputs / trace）让并行写入零冲突
- 并行是图拓扑语义（add_edge 声明），不是代码里的 asyncio.gather——声明与执行分离，可审计
- checkpointer 注入（compile 参数），支持 resume + 流式增量回调（seen_steps）
- 当前用 InMemorySaver（重启丢失，生产应换 Redis/MySQL Saver）

### 6.2 修改工作流 `PlanRevisionWorkflow`（revision_workflow.py）

**定位**：对已有计划做局部修改（低成本、条件路由、单分支）

**8 节点（路由1 + 分支5 + 后处理2）**：
```
START
  → revision_intent（路由判定：按 operation 类型选分支）
  → conditional_edges → 5 分支之一:
      meal / shopping / budget / constraint / compound
  → affected_agents（声明本次修改影响了哪些领域能力，供预览/审计）
  → dependency_sync（标记依赖屏障完成）
  → END
```

**关键设计**：
- 5 个分支节点全绑同一个 `_execute_selected_branch`——路由价值在**声明受影响能力**（requires）而非跑不同代码
- **按影响面分级路由**：`update_budget` 只路由到 BUDGET（仅预算+校验），`adjust_macro_target`/`exclude_ingredient` 路由到全部 4 项能力
- 无 checkpointer，一次性 ainvoke
- 只产预览草稿，不落库（落库要等 confirm）

### 6.3 为什么是两套

**整段重生成 vs 局部修改是两种本质不同的计算**：
- 主工作流是"高成本推倒重来"（11节点 + 3次LLM + 双路检索），改"周三晚餐换鸡胸肉"既慢又可能改乱用户满意的部分
- 修改工作流是"低成本精准手术"（路由 + 确定性应用，不调 LLM 生成新菜）

两者通过"预览/确认分离"协议衔接，避免"AI 自作主张直接改库"。

---

## 七、RAG + 记忆 + 实体抽取 + 断点续传

### 7.1 RAG 双路混合检索

`KnowledgeService.retrieve()` 用 `asyncio.gather` 并发跑两路：

- **图谱路（Neo4j，管硬约束）**：Cypher 精确匹配忌口、食材替代关系——确定性关系用图精确匹配，不被向量模糊
- **向量路（Milvus/Chroma，管语义）**：BGE-M3 嵌入（归一化）→ 广召回 top_k×3 候选 → bge-reranker-v2-m3 二阶段精排 → 取 top_k

任一路径异常只返回空 + 状态标记（vector_status / neo4j_status / rerank_status），绝不抛错。

### 7.2 实体抽取

`entity_extractor.py` 两级抽取：
- **LLM 级**：temperature=0、response_format=json_object、max_tokens=1024，抽实体（kind, value）+ 关系三元组（subject, relation, object），文本截断 6000 字符
- **正则级**：零依赖兜底，匹配"类型:值"行式模式，只抽实体不抽关系

LLM 失败自动切回正则，保证图谱永不为空。关系三元组是 LLM 级的独特价值（正则抽不出"菜需要食材"这种结构化关系）。

### 7.3 记忆三层架构

| 层 | 介质 | 内容 | 生命周期 |
|---|---|---|---|
| L1 短期 | WorkflowState + checkpoint | 单次规划会话状态 | 进程内，重启即失 |
| L2 中期 | Neo4j + 向量库 | 可检索的语义与关系、反馈滚动文档 | 可独立重建 |
| L3 长期 | MySQL | UserProfile/NutritionGoal/TasteProfile/对话历史 | 系统唯一可信源 |

**为什么分三层**：短期状态用 checkpoint 零成本；中期图谱/向量承载"可被检索的语义与关系"，与主链路解耦；长期 MySQL 存"事实与偏好"。分层让每层独立降级——图谱挂了仍有向量+MySQL，向量挂了仍有图谱+MySQL。

### 7.4 断点续传

`workflow.py` 的 `run()` 双分支：
- **resume=True**：`aget_state(config)` 取快照，`seen_steps = len(trace)` 用于增量回调（已执行 trace 不重复回调）
- **resume=False**：构造 graph_input，astream 流式执行，每帧增量调 on_step
- `thread_id = run_id` 作会话标识，LangGraph 据此在 checkpointer 定位状态

**当前局限**：InMemorySaver 进程内存储，重启丢失；生产应换 RedisSaver/AsyncSqlSaver。

---

## 八、工程化与交付

### 8.1 前端工程化

- 10 个视图：AuthView / HomeView / KnowledgeView / NutritionGoalView / PlanDetailView / PlannerView / ProfileCollectionView / RecipeDetailView / ShoppingView / WeeklyReportView
- 路由懒加载 + chunk 错误重试
- 工程化门禁：vue-tsc 零错误 + 18 Vitest 通过 + Vite build 通过
- 三栏布局 + diff 预览（局部修改前后对比）

### 8.2 后端工程化

- 87 个 API 端点（认证 12 + 业务 75），按功能域分组
- alembic 迁移（0001 初始 + 0002 删 6 遗留表）
- GitHub Actions CI 六道门禁：ruff / mypy / pytest / alembic / frontend / rag-eval
- 食材营养库：2043 条（来源《中国食物成分表第6版》Qwen2.5-VL-72B OCR 结构化，v2.0.0）+ 12 条 recipes + 61 个 china_food 分类 + 食材替代关系 JSON

### 8.3 交付前必做 5 项

1. 修复后端全量测试（计划校验断言 + 分段生成注入 fake model + 临时目录权限）
2. checkpoint 换 Redis/MySQL Saver 做持久化
3. 密钥走 Secret 管理
4. 迁移策略：生产只跑 `alembic upgrade head`
5. CI 干净环境全绿

---

## 九、踩坑与解决方案

### 9.1 Mock 测试掩盖 Chroma 接口 bug（真实踩坑）

**现象**：单元测试全绿，真实端到端冒烟测试直接挂。
**根因**：embeddings.py 的 SentenceTransformerEmbedding 与 Chroma 1.5.9 接口不兼容（缺 embed_query、返回类型错），单元测试把 Chroma stub 掉了永远测不出。
**解决**：改回返回 model.encode 原始 numpy + 补 is_legacy=False；把真实 RAG 冒烟测试加进 CI。
**教训**：Mock 测试替代不了真实端到端 smoke test，关键链路必须有真实集成测试。

### 9.2 去家庭化导致忌口校验断链（真实踩坑）

**现象**：项目从家庭导向收敛成独居单人后，忌口校验失效。
**根因**：校验器和餐食智能体还在读 `MemberProfile` + members 列表（去家庭化后 members 恒空），单元测试也 stub 了空成员，没发现。
**解决**：改成读单人的 `UserProfile.constraints`。
**教训**：产品定位一变，数据流要走查一遍，不能只改表名。

### 9.3 venv/uv 重建陷阱

**现象**：PyCharm 报 CreateProcess error=2，.venv/Scripts 缺失。
**根因**：中断的 pip install 留下半成品；uv sync 会 build casamind-api 触发 sandbox SAFE_DELETE_FAIL_CLOSED。
**解决**：固定流程 `uv venv --allow-existing --python 3.12` → `uv sync --no-install-project --extra ai --extra dev` → `uv pip install --reinstall` 补隐式依赖。

### 9.4 MySQL 方言适配

**问题**：从 PostgreSQL 迁到 MySQL，Postgres-only 特性不可用。
**解决**：移除 postgresql_where / 数组 / Postgres-only server_default，JSON 列用 `default=list` 兼容；db/session.py 按方言区分连接池参数（pool_size 等仅非 SQLite 传入）。

### 9.5 数据表清理 20→14

**问题**：去家庭化后有 6 张遗留表（calendar_events / plan_tasks / plan_budgets 等）。
**解决**：alembic 0002 迁移删除；工作流 13→11 节点（移除 task/calendar）；checkpoint 迁 InMemorySaver（移除 PostgreSQL 依赖）。

### 9.6 checkpoint 重启丢失

**问题**：InMemorySaver 进程内存储，重启后工作流中间态丢失。
**现状**：反馈已落 MySQL，画像从 plan_feedback 实时重算不受影响；但 resume 场景会失败。
**解决**：生产换 RedisSaver / AsyncSqlSaver（MySQL）。

### 9.7 文档口径矛盾

**问题**：项目分析报告 v9.3 称"55+ 全绿可交付"，但当天代码审查实测 pytest 未通过。
**原则**：以当天实测执行为准，不以历史文档为准。

---

## 十、面试问答要点

### 10.1 一句话总结

**"约束式生成 + 确定性兜底 + 反馈学习"**——LLM 只管创意，代码管正确性，任何外部依赖挂了只降级不崩，反馈在有无模型两种状态下都被学习。

### 10.2 高频问题应答

| 问题 | 一句话接住 |
|---|---|
| 最大难点 | 让 LLM 的"创意"和代码的"正确性"不打架——约束式生成 + Verifier 三级自愈 |
| 为什么不用一个大模型直接生成 | 约束边界不同、降级粒度不同、需独立演化；且领域智能体无写库权限 |
| 没有 LLM 还能用吗 | 能。每个智能体有确定性回退 + taste_profile 双路生效 |
| 踩过最深的坑 | Mock 测试掩盖 Chroma 接口 bug——单元测试全绿，真实冒烟直接挂 |
| 数据一致性怎么保证 | MySQL 事务保主数据；Neo4j/向量库可降级可重建；反馈落库是唯一不可降级主链路 |
| 为什么用 LangGraph | 并行是拓扑语义、状态显式流转、checkpoint/resume 免费、图即文档 |
| 为什么用 SSE 不用 WebSocket | AI 对话是单向流，SSE 纯 HTTP + Nginx 原生支持 + 浏览器自带断线重连 |
| 重做会改什么 | checkpoint 换持久化 Saver、补测试体系、密钥入 Secret、向量同步改 MQ |

### 10.3 三个最值得讲的技术点

1. **LangGraph 11 节点多智能体工作流**：用图结构把并行、状态流转、断点续传变成声明式能力
2. **约束式生成 + 确定性兜底**：LLM 管创意、代码管正确性，EAFP 全链路降级
3. **反馈闭环的双路学习**：taste_profile 在"有模型"和"无模型"两种状态下都被消费

---

*配套文档：《SoloChef_项目讲解话术.md》（口语讲解版）｜《SoloChef_技术全量分析报告.md》（深度原理版）｜《SoloChef_交付评估与技术全量分析报告.md》（交付评估版）*

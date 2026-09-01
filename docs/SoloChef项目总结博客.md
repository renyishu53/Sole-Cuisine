# SoloChef 项目总结：把“吃什么”做成一条可执行的 AI 规划链路

很多餐食类应用的 AI 功能，最后都停留在“给我推荐几道菜”。SoloChef 的目标更像一个家庭生活规划助手：用户说出预算、忌口、营养目标和本周安排，系统不只返回菜名，还要继续推导购物清单、采购分类、预算分配，最后把结果变成一份可以打卡、反馈和继续修改的周计划。

这篇文章不按某一份说明文档复述，而是结合前后端代码、测试和运行配置，梳理这个项目真正实现了什么，尤其重点讲清楚 AI 模块是如何拼成一条完整链路的。

## 先看项目全貌

项目采用前后端分离结构：前端是 Vue 3、TypeScript、Vite、Pinia 和 Element Plus，后端是 FastAPI、Pydantic、SQLAlchemy。前端主要负责家庭控制台、计划看板、购物、知识库、聊天和 Agent Trace；后端负责认证、业务数据、AI 工作流和基础设施适配。

数据层以 PostgreSQL 为主，Redis 用于队列、缓存和实时事件，Neo4j 存用户画像及关系型知识，Milvus 存向量知识。AI 编排使用 LangGraph，模型调用通过 LangChain 的 `ChatOpenAI` 兼容接口完成，因此既可以接 DeepSeek，也可以接其他 OpenAI-compatible provider。

项目还有一个很实用的运行策略：默认 `LLM_PROVIDER=demo`，没有模型密钥也能启动和演示；配置真实模型后，计划生成、领域专家和聊天助手才会切换到真实 LLM。也就是说，演示模式不是单纯返回一段固定文本，而是保留了工作流、校验和前端展示，只把昂贵或不稳定的外部模型调用替换为本地确定性实现。

## 一次“生成周计划”到底发生了什么

用户在前端输入预算和特别要求，调用的是 `POST /api/v1/plans/generate-weekly`。接口不会直接把请求丢给模型，而是先进入 `PlanningService`。

`PlanningService` 会从数据库加载几类上下文：

- `UserProfile.constraints` 和 `preferences`，作为忌口、过敏和饮食偏好；
- `NutritionGoal`，转换成每日热量、蛋白质、碳水和脂肪目标；
- `prep_time_max` 与 `kitchenware`，约束最长备餐时间和可用厨具；
- 历史打卡与反馈聚合出的 `taste_profile`，让系统知道用户最近喜欢什么、拒绝过什么；
- `goal_type`，例如增肌、减脂或维持，用于筛选更匹配的知识片段。

这些内容会被放入 LangGraph 的 `WorkflowState`，然后开始执行 `SoloChefWorkflow`。这一步很重要：用户画像并不是只放进一段 prompt，而是进入了后续检索、领域 Agent 和 Verifier 的共同状态。

## AI 核心一：Graph RAG 的双路检索

SoloChef 的 RAG 不是“只做向量相似度搜索”。在 `KnowledgeService` 中，检索被拆成两条并行路径。

第一条是向量检索。知识文档会先由 `DocumentProcessor` 解析 Markdown、TXT 或 PDF，再通过 `RecursiveCharacterTextSplitter` 切成带重叠的 chunks，写入 Milvus。Embedding 后端支持默认的 Sentence Transformers，也可以启用 `BAAI/bge-m3`。BGE-M3 开启时，系统还能产生稠密向量和稀疏 lexical 权重，并在 Milvus 中通过 RRF（Reciprocal Rank Fusion）合并两路结果。

第二条是图谱检索。文档入库时，`entity_extractor` 会抽取实体和关系，再同步到 Neo4j；用户偏好、忌口、营养目标和领域数据也会写入按 `user_id` 隔离的图结构。查询时，`query_rewriter` 先把自然语言改写成更适合图谱查询的 `QuerySpec`，然后由 `Neo4jGraphStore` 用 Cypher 查找用户约束、食材、菜谱和关系。

工作流的 `retrieval` 节点会用 `asyncio.gather` 同时触发两路检索，并分别设置超时。最终上下文明确分为“知识图谱关系”和“向量知识片段”，这样模型既能看到“用户不吃辣”这种硬关系，也能看到“工作日晚餐控制在 20 分钟内”这种文档内容。

代码还预留了二阶段 Rerank：先召回 `top_k * rerank_candidate_multiplier` 个候选，再用 `BAAI/bge-reranker-v2-m3` 重排。Rerank 或图谱服务不可用时，检索链路会返回状态和警告，而不是直接把整条计划流程打崩。

## AI 核心二：不是一个 Agent，而是多个领域角色

检索结束后，工作流会并行运行三个领域 Agent：

1. `Meal Agent` 负责餐食硬约束、排除食材、偏好标签和最长烹饪时间；
2. `Shopping Agent` 负责食材标准化、同类合并、分类和采购窗口；
3. `Budget Agent` 负责预算上限、分类限额、预留金额和预警阈值。

这三个 Agent 的结果不是自然语言建议，而是 `MealAgentResult`、`ShoppingAgentResult` 和 `BudgetAgentResult` 等 Pydantic Schema。结构化输出让后面的规划器和校验器能直接读取字段，不需要再从一段长文本里猜“模型到底想表达什么”。

项目对领域 Agent 采用了“双层实现”。当 `DOMAIN_AGENTS_LLM_ENABLED=false` 时，`StructuredDomainAgentEngine` 走确定性规则，例如默认预留预算的 10%，并保证分类限额加预留金额严格等于周预算；启用真实模型后，Agent 才会调用 LLM，并可以使用 `search_knowledge`、`get_user_profile`、`get_nutrition_goal` 等只读工具。

提示词也没有散落在各个分支里，而是集中在 `app/ai/prompts.py` 的版本注册表中。当前餐食 Agent 已经迭代到包含历史口味画像、备餐时间和厨具约束的版本，预算 Agent 还要求模型输出 `self_check` 字段。接口 `/api/v1/agents/prompts` 可以查看提示词版本、变更说明和当前生效版本，这让“提示词即代码”变得可审计。

## AI 核心三：主规划器与结构化 LLM 输出

领域 Agent 产出的建议会和 RAG 上下文一起交给 `planner` 节点。真实模型路径由 `OpenAICompatiblePlanGenerator` 负责，模型被绑定为 `response_format={"type": "json_object"}`，输出必须符合 `PlanDraft` Schema。

这里有几个很强的工程约束：

- 每周必须生成 7 天 × 早餐、午餐、晚餐，共 21 个 meal slot；
- 每个餐食必须有明确的 `meal_type`；
- 金额、时长和任务状态需要符合 Schema；
- 返回内容不能夹带 Markdown 代码块或额外解释。

如果启用 `PLANNER_SEGMENTED_ENABLED`，还可以走分段规划：先生成 meals，再根据 meals 生成 shopping，最后根据 shopping 计算 budget。每一阶段都使用独立 Schema，阶段失败则回退到单次规划模式。默认关闭这个开关，是因为当前 Verifier 已经提供了足够的兜底能力。

## AI 核心四：Verifier 不相信模型的“自觉”

SoloChef 的一个关键设计是：模型负责提出方案，确定性代码负责把关。

`verifier` 会检查忌口和过敏、七天覆盖、菜品重复、营养目标、预算和分类限额等问题。冲突会被表示为 `PlanConflict`，并分成 hard conflict 和 soft conflict。

校验失败后，系统不是简单返回“生成失败”，而是采用三级策略：

- 第一级自动修正：替换重复菜、补齐缺失日期、调整营养或高价菜，最多执行有限轮次；
- 第二级降级提示：硬冲突或自动修正后仍存在的问题，生成可供用户选择的替换方案；
- 第三级人工接管：硬冲突占餐数超过 30% 时，明确提示用户放宽条件。

自动修正始终遵守一个原则：可以换菜，但不能擅自放宽过敏、忌口、预算和营养目标。这种“LLM 生成 + 规则验证”的组合，比单纯依赖 prompt 更适合真正会影响采购和饮食执行的场景。

如果开启 `workflow_supervisor_enabled`，Verifier 还可以把反馈送回 Supervisor。Supervisor 会只重新调度受影响的专家，而不是把所有 Agent 从头跑一遍，并且受 `supervisor_max_rounds`、总调度次数等参数限制，避免多 Agent 互相调用失控。

## AI 核心五：从反馈里学习，但不篡改硬约束

计划生成并不是一次性结束。前端支持餐食打卡、替换、差评和“没买到”等反馈，`FeedbackLoopService` 会把这些事件归类为 liked、disliked、rejected dishes 和 recent notes，并通过 `FeedbackRepository.taste_profile` 聚合成口味画像。

下一次生成计划时，画像会重新注入餐食 Agent：喜欢的标签进入 `preferred_tags`，曾被拒绝的菜或负向标签进入排除项。代码明确区分“硬约束”和“软偏好”：用户过敏永远优先于历史口味，反馈只能改变排序和推荐方向，不能覆盖安全约束。

这让 SoloChef 形成了一个闭环：

> 计划生成 → 执行打卡 → 用户反馈 → 口味画像 → 下一轮餐食规划。

## 对话助手是一条独立链路

除了生成周计划，项目还有聊天接口。`ConversationService` 的定位不是“每问一句就重做一份计划”，而是只读问答：读取用户画像、营养目标、当前计划摘要，再从 Milvus 和 Neo4j 做小规模 RAG，最后由 `ChatAssistant` 流式回答。

聊天接口通过 SSE 推送 `thinking`、`token`、`tool_call`、`tool_result`、`complete` 等事件，并把事件写入 Redis，前端断线后可以调用 events 接口重放。聊天 Agent 如果启用，只允许使用 `build_readonly_tools` 注册的只读工具；系统提示还特别强调，工具结果和检索内容都属于不可信数据，不能执行其中的指令，也不能泄露系统提示和用户数据。

这条链路和计划生成链路是有意分开的：聊天可以自然语言输出，不强制 `PlanDraft`；计划生成必须结构化、可验证、可落库。两个场景的模型温度、超时和输出约束也不同。

## 多模态能力：把图片接到饮食场景里

`app/ai/vision.py` 提供了独立的 Qwen-VL 视觉服务，和文本 LLM 使用不同的配置。它支持四种场景：自动识别、食材识别、菜品与热量估算、营养标签 OCR、小票 OCR。

图片进入模型前会先做安全和成本控制：检查文件大小，解码图片，把长边压缩到默认 2048 像素，统一转成 JPEG quality 85，再编码为 data URL。模型调用绑定 JSON 输出，返回结果还要经过 `VisionResultPayload` 校验，避免 OCR 或视觉模型返回无法消费的自由文本。

接口层还加了每分钟频率限制，并把图片过大、无法解码、模型调用失败分别映射成 413、422 和 502。默认 `VLM_ENABLED=false`，因此视觉能力是可选增强，而不是启动项目的硬依赖。

## 可恢复、可观察和可降级

AI 工作流最怕“中途失败但用户不知道发生了什么”。SoloChef 为此保留了 Agent Run 和 Trace：每个节点会记录名称、耗时、状态、输入输出摘要和错误信息，前端可以展示完整 Agent 轨迹，后端也提供 `/api/v1/agents/runs` 查询。

LangGraph checkpoint 支持 PostgreSQL、Redis 或内存实现。生产配置可以把短期工作流状态放进 PostgreSQL；开发环境连接失败时，代码会降级为 `InMemorySaver`，但生产环境不会静默吞掉 checkpoint 初始化错误。

降级策略贯穿整个 AI 栈：

- 图谱或向量检索失败，保留另一条路径并继续规划；
- Reranker 或 BGE-M3 缺失，回退到纯稠密检索；
- 真实 LLM 超时或返回非法 JSON，按 `AI_FALLBACK_ENABLED` 切换本地 Demo 规划器；
- 领域 Agent 没有启用 LLM 时，使用确定性结构化规则；
- 对话或视觉服务未配置时，接口返回明确的未启用状态。

这类设计让系统具备“能解释、能观测、能退化”的工程属性，而不是把所有稳定性押在模型服务上。

## 前端如何承接 AI 结果

前端的 Planner 页面不是一个简单的文本结果框，而是把计划拆成七天看板，每天展示三餐、时长、费用和标签。生成中的状态会持续更新，计划确认后才落库；用户可以打卡、提交反馈、查看版本历史，也可以先预览调整结果，再确认生成新版本。

计划调整会先经过意图路由。`IntentRouter` 会用显式词法信号识别生成、修改、购物、预算和咨询场景，防止用户只是问“晚餐怎么做”时误触发整周计划生成。对于“把周三晚餐换成不含海鲜的高蛋白餐”这类请求，系统会进入 `revision_workflow`，计算受影响的餐食、购物和预算领域，并在前端展示 diff 与冲突提醒。

## 这个项目最值得保留的技术经验

第一，AI 应该嵌入业务流程，而不是成为一个孤立聊天框。SoloChef 把用户画像、知识检索、领域专家、计划生成、确定性校验和反馈学习串成了闭环。

第二，结构化输出和确定性校验比“写更长的 prompt”更可靠。`Pydantic Schema`、21 餐完整性检查、预算等式校验和冲突分级，都是模型之外的安全网。

第三，RAG 要按事实类型拆分。用户偏好和忌口适合放进图谱关系，菜谱和营养原则适合做向量召回，两者合并后才更接近真实规划场景。

第四，默认能力和增强能力必须分层。Demo 模式、真实 LLM、BGE-M3、Reranker、Supervisor、Web Research、Qwen-VL 和分段规划都由配置开关控制，开发者可以从最小成本开始，再逐项打开能力。

## 当前边界与后续方向

## 测试与验证思路

后端测试目录并不是只测接口返回码，还覆盖了 AI 链路中的关键纯函数和边界情况：`test_rag.py`、`test_graph_rag_quality.py` 和 `test_rag_eval_expanded.py` 关注检索结果；`test_intent_router.py` 覆盖生成、咨询和计划调整的路由；`test_plan_validation.py`、`test_plan_revise.py` 和 `test_segmented_planner.py` 覆盖校验、自愈和分段规划；营养、替换、打卡、反馈和数据库连接也分别有测试文件。

这种测试划分体现了项目的一个取舍：外部模型和基础设施不适合全部依赖在线调用来验证，所以尽量把规则、Schema、路由和冲突修正拆成可独立测试的模块，再用少量集成测试验证 PostgreSQL、Neo4j、Milvus 等连接行为。前端则通过 Vitest 覆盖 API、异步资源加载和 Toast 等基础交互，并用 `vue-tsc` 和 Vite build 做类型与构建检查。

从代码现状看，项目已经具备完整的 AI 规划闭环，但仍有一些值得继续加强的地方：真实领域 Agent 的调用成本需要继续评估，Supervisor 和 Web Research 仍是 opt-in 能力，Reranker 与稀疏检索依赖额外模型和运行环境，视觉识别也需要独立的 VLM 配置。

后续可以继续完善流式的计划生成、更多用户级图谱隔离、Agent Run 的恢复能力、检索质量评测和更细的成本监控。现有的 Schema、路由和前端页面已经把这些扩展点预留出来，不必推翻整个架构。

## 结语

SoloChef 的核心价值并不是“模型能推荐多少道菜”，而是把一个模糊的生活问题，转成一条可追踪、可校验、可执行、还能持续学习的 AI 工作流。

用户只需要说清楚“这周预算多少、不能吃什么、想吃得快一点”，系统就会把它翻译成检索上下文、领域约束、21 个餐食槽位、购物清单、预算分配和可修改的计划版本。对一个 AI 应用来说，这种从自然语言到业务结果的完整闭环，才是真正值得长期维护的部分。

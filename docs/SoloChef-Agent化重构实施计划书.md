# SoloChef Agent 化重构实施计划书

> 状态：阶段 1、3–6 的代码已落地；本地全量测试和生产灰度验证待环境执行。  
> 基线：11 节点静态 LangGraph 周计划工作流、固定上下文流式问答、确定性 Verifier。  
> 原则：先稳定压缩既有编排，再以默认关闭的旁路能力引入 Agent；任何外部信息都不得绕过规则终审。

## 1. 目标与边界

目标架构分为三层：

```text
问答：只读 ReAct Agent（按需调用内部查询与可选外部搜索）
生成：supervisor 调度的多专家协作（默认仍可走静态路径）
终审：确定性 Verifier（21 餐、预算、忌口、营养与人工接管规则）
```

本期不把 Verifier Agent 化，不为 Agent 注册写工具，不允许外部结果直接写入预算金额、采购价格或校验结论。

## 2. 现状与关键结论

- 当前 `constraint_parser` 结果 `planning_constraints` 未被下游消费，可移至服务层预处理。
- `graph_retriever`、`vector_retriever` 与 `coordinator` 可合为一个 `retrieval` 节点，保留双检索并发、各自状态和降级信息。
- `domain_coordinator` 可内联到 `planner`，但必须持续写回 `domain_bundle` 与 `domain_context`：Verifier 和 API 响应依赖前者。
- `final_planner` 可合入 `verifier` 尾部，负责来源汇总。
- 所以静态图应从 11 节点压缩为 **6 节点**，不是 5 节点；三位领域专家仍为三个独立节点。
- LangGraph checkpoint 当前使用 `InMemorySaver`，只支持同一进程生命周期内的恢复；数据库记录的是运行元数据，不能跨进程恢复图状态。

目标静态拓扑：

```text
START -> retrieval
      -> [meal_agent, shopping_agent, budget_agent]
      -> planner
      -> verifier
      -> END
```

## 3. 切换策略（清理旧数据）

新部署起所有计划固定使用当前唯一工作流。旧图和旧 run 不再兼容；发布前清理 `agent_runs` 历史数据，避免应用继续承担迁移和恢复负担。

- 应用不读取、不迁移、不恢复旧 run；需要继续执行时重新生成新计划。
- 旧数据清理必须作为发布前明确执行的一次性运维操作，不放在应用启动流程中。

## 4. 实施阶段

### 阶段 0：版本与回归护栏

**目标**：为并存、对比和回滚建立基础。

变更：

- 生成路径只有一套当前工作流；恢复接口直接关闭。
- 为测试构造稳定的检索器与 PlanGenerator mock。

验收：

- 旧 run 不再作为受支持的数据对象；重试接口返回 409 并提示重新生成。
- 不打开新开关时，现有 API 行为和 trace 不变。

### 阶段 1：静态工作流 11 -> 6

**目标**：在不改变最终业务结果的前提下，去除不具备独立恢复或决策价值的图节点。

变更：

- `PlanningService.generate()` 在创建 run 前解析约束；如后续仍无消费者，则不再写入工作流状态。
- 新 `retrieval` 节点用 `asyncio.gather` 并行图谱/向量检索，保留各自 `status`、hits、独立异常降级和融合 context。
- planner 开头构造 `DomainAgentBundle`、`domain_context`，再生成 draft。
- verifier 尾部生成 sources，写回 `sources` 与 trace。
- 保持 `domain_results` / `specialist_outputs` reducer、三路专家并行及确定性校验不变。

验收：

- compact trace 正好为 6 个阶段：`retrieval`、三个专家、`planner`、`verifier`。
- 固定 mock 下，两图的 `meals`、`shopping`、`budget`、`conflicts`、`domain`、`sources` 相同。
- 更新旧测试中对 11 步与旧名称的断言，并新增 compact 版本断言。
- retry 在同一进程内仅从 compact 失败节点继续，不重复已完成检索。

### 阶段 2：生产验证与默认切换

**目标**：降低压缩图切换风险。

变更：

- 在测试、预发布和生产灰度环境分别验证 compact 输出、SSE 轨迹、失败记录和审计只读行为。
- 对业务字段做结构化回归；trace 数量、名称与时长只验证 compact 的 6 阶段契约。
- 通过验证后保持 compact 为唯一生产拓扑；不再保留旧图回退开关。

验收：

- 全量 `pytest` 与 `ruff check .` 通过。
- 差异报告只包含预期的 trace/checkpoint 元数据变化。
- 出现生产异常时，回滚应用版本；数据库中的历史记录仍保持只读审计。

### 阶段 3：内部只读工具层

**目标**：建设可复用、权限闭环的工具基础设施，不改变默认问答。

新增 `app/ai/agent_tools.py`：

```python
@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, object]
    handler: Callable[..., Awaitable[str]]
    external: bool = False
```

首批工具：

- `get_user_profile`
- `get_nutrition_goal`
- `get_active_plan`
- `get_nutrition_report`
- `search_knowledge`

约束：

- `user_id`、数据库 session、密钥全部通过闭包注入，LLM 参数 schema 不暴露这些字段。
- 只注册只读工具；计划确认、库存变更和数据写入继续只经显式 API。
- 每个工具有超时、异常文本化、结果截断和结构化审计记录。

验收：

- 工具单测覆盖用户隔离、空数据、异常、超时与截断。
- 未启用 Agent 时工具层不参与原对话或计划生成。

### 阶段 4：问答 ReAct Agent

**目标**：让问答在开启时由模型按需拉取上下文，而非无条件注入全部画像和 RAG。

新增配置：

```python
chat_agent_enabled: bool = False
chat_agent_max_iterations: int = 6
chat_agent_tool_timeout_seconds: float = 8.0
```

变更：

- `ChatAssistant.answer()` 支持 `bind_tools` 循环，最大 6 轮。
- SSE 增加 `tool_call`、`tool_result` 事件；前端将其显示为可展开的过程记录，而非静默忽略。
- 达到轮次上限、工具失败或模型不支持 tool calling 时，回退为当前固定上下文问答。

验收：

- 单轮调用事件顺序为 `tool_call -> tool_result -> token`。
- 永远请求工具的 mock 恰好在第 6 轮停止并输出兜底回答。
- 工具结果作为数据而非指令进入 `ToolMessage`；系统提示明确其不可信属性。

### 阶段 5：supervisor 多 Agent

**目标**：在当前静态工作流上增加可选的动态专家调度，不破坏默认路径。

新增配置：

```python
workflow_supervisor_enabled: bool = False
supervisor_max_rounds: int = 2
supervisor_max_dispatches_per_round: int = 4
supervisor_max_total_dispatches: int = 6
```

当前决策载荷：

```python
{
  "dispatch": ["meal_agent", "shopping_agent", "budget_agent", "web_research"],
  "reason": "string"
}
```

实现约束：

- 首轮固定并行三个领域专家，保证 Planner 有完整领域基线；补轮由 supervisor 动态选择需要重跑的专家。
- Verifier 的条件边决定“继续协商或结束”；每一轮仍必须经过 Planner，不能让 supervisor 直接跳过计划生成并 `finalize`。
- 专家接收可选 `directive`，补轮时使用 supervisor 的定向反馈。
- 拒绝非法专家名、空派发、重复无效派发；专家失败降级为可审计结果。
- 每轮决策、派发与汇聚全部进入 trace；静态路径仍可独立运行。

验收：

- 过敏冲突 mock 触发第二轮，第二轮只重跑 meal 专家，并向其传入 Verifier 反馈。
- 只有“实时需求且本地向量检索为空”时，`web_research` 才进入可派发池。
- 最大轮次和总派发上限始终生效。

### 阶段 6：外部联网研究工具

**目标**：最后接入 `web_research`，使其成为受 supervisor 调度的工具型专家。

新增配置：

```python
tool_websearch_enabled: bool = False
tool_websearch_provider: Literal["tavily", "bocha"] = "tavily"
tool_websearch_api_key: str = ""
```

规则：

- 仅当需求涉及时令、价格或新品类，且本地检索不足时才允许调度。
- 返回 `ResearchResult`：provider、title、url、snippet、fetched_at、status；每项带来源。
- 外部内容限制长度、移除控制字符、标记为不可信数据；失败返回 warning，不中断计划。
- `web_context` 仅进入 planner 建议上下文，不能写入确定性预算、采购单价或 Verifier 判定。
- Tavily 可达性和供应商稳定性先做真实冒烟；不满足要求时切换 Bocha 适配器。

验收：

- 使用 `httpx.MockTransport` 覆盖成功、超时、429、500 与非法响应。
- 无 key、开关关闭、外部失败时生成链路继续执行。
- trace 明确展示 provider、来源数量、截断和 warning 状态。

## 5. 非目标与延后项

- `planner_selfeval` 延后。它会增加一个与 supervisor 重叠的循环、状态和延迟；应先基于 supervisor 的真实质量数据决定是否需要。
- 跨进程恢复延后。需要将 `InMemorySaver` 更换为 Redis 或其他持久化 checkpoint 后端，不能仅依靠业务库 checkpoint 字段实现；Redis 持久化本身也不等同于恢复 API，仍需定义恢复入口、单 run 并发锁、TTL 和审计策略。
- 不承诺“全项目都是 Agent”。静态计划生成和确定性 Verifier 保持为受控编排与规则系统。

## 6. 全局质量门禁

每阶段合并前必须满足：

- 默认开关关闭时，既有核心 API 输出不回归。
- 新增状态有 Pydantic/schema 验证，避免任意 dict 扩散。
- 外部调用具有 timeout、错误分类、截断、日志与 trace。
- trace 中不得存 API key、完整隐私数据或未裁剪的外部原文。
- 运行 `pytest`、`ruff check .`；涉及前端事件展示时运行 `npm run build`。

具体启动、开关顺序、SSE 验证和回滚命令见《SoloChef Agent 能力启用与验证手册》。

## 7. 交付顺序与回滚

| 顺序 | 交付物 | 默认状态 | 回滚方式 |
| --- | --- | --- | --- |
| 0 | 清理旧 run 与单一拓扑 | 开启 | 重新生成新计划 |
| 1 | 当前静态图 | 开启 | 修复当前实现 |
| 2 | 生产验证与灰度 | 开启 | 暂停发布并回滚应用版本 |
| 3 | 内部只读工具层 | 未接线 | 不注册工具 |
| 4 | 问答 ReAct | 关闭 | `chat_agent_enabled=false` |
| 5 | supervisor | 关闭 | `workflow_supervisor_enabled=false` |
| 6 | web research | 关闭 | `tool_websearch_enabled=false` |

最终叙事应准确表述为：路径明确的计划任务采用静态受控编排；开放问答使用只读 ReAct；复杂计划仅在开启时由 supervisor 动态协作；所有结构化计划仍由确定性 Verifier 终审。

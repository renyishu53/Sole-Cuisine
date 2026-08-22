# SoloChef Agent 能力启用与验证手册

## 1. 启动基础服务

在项目根目录创建 `.env`，至少配置 MySQL、JWT 和前端 API 地址：

```env
DATABASE_URL=mysql+aiomysql://solochef:solochef_password@localhost:3306/solochef
JWT_SECRET_KEY=替换为32位以上随机字符串
CORS_ORIGINS=["http://localhost:5173"]
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

启动 MySQL；需要完整 RAG 时再启动 Redis、Neo4j、Milvus，并配置对应连接信息。

后端：

```powershell
cd backend
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000
```

前端另开终端：

```powershell
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`。

## 2. 先验证静态计划

保持以下开关关闭，确认基础链路稳定：

```env
LLM_PROVIDER=demo
CHAT_AGENT_ENABLED=false
WORKFLOW_SUPERVISOR_ENABLED=false
TOOL_WEBSEARCH_ENABLED=false
```

在页面生成一周计划，检查：

- 生成 21 餐；
- 轨迹包含 `retrieval`、三个领域专家、`planner`、`verifier`；
- 预算、忌口和营养校验仍由 Verifier 完成；
- `GET /api/v1/agents/runs` 能看到本次运行记录。

## 3. 启用真实 LLM

配置兼容 OpenAI API 的模型：

```env
LLM_PROVIDER=deepseek
LLM_API_KEY=你的密钥
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
```

先调用 `POST /api/v1/ai/llm/smoke` 验证连通性，再生成计划。模型不可用时保留：

```env
AI_FALLBACK_ENABLED=true
```

## 4. 启用问答 ReAct Agent

```env
CHAT_AGENT_ENABLED=true
CHAT_AGENT_MAX_ITERATIONS=6
CHAT_AGENT_TOOL_TIMEOUT_SECONDS=8
```

在问答窗口发送需要画像、营养目标或知识库信息的问题。浏览器开发者工具的 SSE 流应出现：

```text
tool_call -> tool_result -> token -> complete
```

工具只读，不会修改计划、库存或用户资料。关闭 `CHAT_AGENT_ENABLED` 即回到固定上下文问答。

## 5. 启用 Supervisor

```env
WORKFLOW_SUPERVISOR_ENABLED=true
SUPERVISOR_MAX_ROUNDS=2
SUPERVISOR_MAX_DISPATCHES_PER_ROUND=4
SUPERVISOR_MAX_TOTAL_DISPATCHES=6
```

重新启动后生成计划，轨迹中应出现 `supervisor`。Supervisor 只能从三个领域专家中选择，非法名称会被过滤；任何异常都会降级为安全派发，不阻断计划生成。

## 6. 启用 Tavily 联网搜索

```env
TOOL_WEBSEARCH_ENABLED=true
TOOL_WEBSEARCH_PROVIDER=tavily
TOOL_WEBSEARCH_API_KEY=你的Tavily密钥
TOOL_WEBSEARCH_TIMEOUT_SECONDS=8
```

使用包含“本周价格、时令食材、新品类”等实时需求的提示词，确认轨迹出现 `web_research`。结果必须带来源，并只进入 Planner 建议上下文；预算金额、采购价格和 Verifier 结论不得由网页内容直接覆盖。

## 7. 验收命令

```powershell
cd backend
$env:TMP=(Resolve-Path .pytest-tmp).Path
$env:TEMP=$env:TMP
.\.venv\Scripts\pytest.exe tests/test_rag.py tests/test_api.py -q
.\.venv\Scripts\ruff.exe check app/ai/agent_tools.py app/ai/workflow.py

cd ..\frontend
npm run build
```

## 8. 回滚

发现 Agent 行为异常时，依次关闭：

```env
TOOL_WEBSEARCH_ENABLED=false
WORKFLOW_SUPERVISOR_ENABLED=false
CHAT_AGENT_ENABLED=false
```

静态计划和确定性 Verifier 不依赖这些开关。旧 `agent_runs` 已按方案清理，不支持恢复；需要重新执行时重新生成计划。

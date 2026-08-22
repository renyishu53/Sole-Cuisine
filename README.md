# SoloChef

阶段 1-3（真实 LLM 校准、PostgreSQL/Alembic、JWT 与家庭隔离）的首次部署和 Yaak 验证见：`docs/阶段1-3实施部署与第三方配置.md`。

阶段 4（家庭成员画像 CRUD、家庭隔离与 Graph RAG 同步）的迁移和 Yaak 验证见：`docs/阶段4-家庭成员画像部署与接口测试.md`。

SoloChef 是一个基于 Graph RAG 与多智能体协同的 AI 餐食与采购规划平台。项目将家庭成员画像、固定日程、餐食、采购、家务和预算放进同一条规划链路，输出可执行的一周家庭计划，并完整展示 Agent 决策轨迹。

## 当前能力

- Vue3 家庭控制台：仪表盘、AI 规划、成员、日历、家务、餐食、购物、预算、知识库、Agent Trace、登录
- FastAPI 类型化接口：仪表盘及各业务模块查询、周计划生成、Agent Run 查询
- 可运行的 Graph RAG 多 Agent 工作流：Intent、双路 Retriever、Coordinator、餐食/采购/家务/预算专家、Planning、Verifier、Final Planner
- 基础设施编排：PostgreSQL（业务库与 LangGraph 短期记忆）、Redis、Neo4j、Milvus
- 双模式运行：默认 Demo 模式不依赖 LLM 密钥；环境变量可切换正式数据与 AI 适配器

## 目录

```text
.
├── frontend/             # Vue3 + TypeScript + Vite
├── backend/              # FastAPI + Pydantic 分层 API
├── docker-compose.yml    # PostgreSQL / Redis / Neo4j / Milvus
├── .env                  # 本地运行配置（不提交）
├── .env.example
├── HomePilot_PRD.md
└── SoloChef UI 设计.md
```

## 本地启动

使用 PyCharm 调试、Docker 部署和 Yaak 测试时，请参考 [PyCharm 调试与 Yaak 测试](docs/PyCharm调试与Yaak测试.md)。项目根目录的 `.run` 已提供可共享运行配置。所有运行配置统一读取项目根目录 `.env`；`.env.example` 仅作为模板，`backend/.env` 和 `frontend/.env` 不再使用。

后端（PowerShell）：

```powershell
cd backend
python -m venv .venv
 .\.venv\Scripts\python.exe -m pip install -e ".[ai,dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

前端：

```powershell
cd frontend
cmd /c npm install
cmd /c npm run dev
```

打开 `http://localhost:5173`。API 文档位于 `http://localhost:8000/docs`。登录演示页位于 `http://localhost:5173/login`。

## AI / Graph RAG

后端现在包含可运行的 Graph RAG 闭环：

- `DocumentProcessor`：解析 Markdown、TXT、PDF，使用 `RecursiveCharacterTextSplitter` 切分文本。
- `ChromaVectorStore`：使用 Chroma `DefaultEmbeddingFunction`（ONNX MiniLM-L6-v2）生成向量并进行 Top-K 检索。
- `Neo4jGraphStore`：把成员、偏好、忌口和日程写成关系图，并用 Cypher 查询家庭硬约束。
- `HouseholdPlanningWorkflow`：真实 LangGraph `StateGraph`，图检索和向量检索并行，随后运行菜单、采购、家务、预算四个领域 Agent，再校验并汇总。
- `ChatOpenAI`：当 `LLM_PROVIDER` 非 `demo` 且存在 `LLM_API_KEY` 时，调用 OpenAI 兼容模型并以 `PlanDraft` 做结构化输出；调用失败时由 `AI_FALLBACK_ENABLED` 控制是否降级。

首次初始化内置家庭知识：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/knowledge/bootstrap
```

主要 AI 接口：

```text
GET  /api/v1/ai/status
POST /api/v1/ai/llm/smoke
POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/knowledge
POST /api/v1/knowledge/bootstrap
POST /api/v1/knowledge/documents/text
POST /api/v1/knowledge/documents/upload  # .md / .txt / .pdf
POST /api/v1/knowledge/search
POST /api/v1/plans/generate-weekly
```

示例检索请求：

```json
{
  "query": "孩子不吃辣，周三要快手，推荐什么晚餐？",
  "top_k": 4
}
```

除 `/health`、`/auth/register`、`/auth/login` 和 `/auth/refresh` 外，接口需要 `Authorization: Bearer <access_token>`；家庭 ID 由 JWT 决定。

真实 LLM 配置示例（DeepSeek 为 OpenAI 兼容接口）：

```dotenv
LLM_PROVIDER=deepseek
LLM_API_KEY=your-key
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
AI_FALLBACK_ENABLED=true
```

## 基础设施

```powershell
docker compose up -d
```

开发环境后端直接运行在宿主机时，`.env` 使用 `localhost:5433` 连接 Compose
映射出来的 PostgreSQL（容器内部仍是标准 `5432`）：

```text
DATABASE_URL=postgresql+asyncpg://solochef:***@localhost:5433/solochef
CHECKPOINT_BACKEND=postgres
CHECKPOINT_POSTGRES_URL=postgresql://solochef:***@localhost:5433/solochef
```

业务表和 LangGraph checkpoint 表会在应用启动时幂等创建。Redis 仅用于队列、
缓存和实时状态，不再承担工作流短期记忆。使用 Docker Compose 启动后端容器时，
Compose 会自动覆盖为 `postgres:5432`，避免容器内错误访问 `localhost`。

`LLM_PROVIDER=demo` 时不需要 LLM 密钥；真实 Graph RAG 检索依赖 Milvus 和 Neo4j。

## 验证

```powershell
cd frontend
cmd /c npm run build

cd ..\backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

## 后续扩展

当前接口合同已经固定，后续可增加流式输出、Agent Run 持久化、用户级图谱隔离、检索 rerank 和真实领域专家结构化输出，而不需要改动现有前端页面。

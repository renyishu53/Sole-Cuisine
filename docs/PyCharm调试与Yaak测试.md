# CasaMind PyCharm 调试、部署与 Yaak 测试

## 1. 在 PyCharm 中打开项目

使用 `File -> Open`，选择项目根目录：

```text
D:\pyton_feisi\project\project_agent
```

不要只打开 `backend` 或 `frontend`，否则共享的 `.run`、`.env` 和 Docker Compose 配置无法一起使用。

## 2. 配置 Python 解释器

进入 `Settings -> Project -> Python Interpreter`，选择 `Add Interpreter -> Existing`：

```text
$PROJECT_DIR$\backend\.venv\Scripts\python.exe
```

如果虚拟环境尚不存在，在 PyCharm Terminal 中执行：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

需要真实 AI 客户端依赖时安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ai,dev]"
```

右键 `backend` 目录，选择 `Mark Directory as -> Sources Root`。运行配置已经把工作目录设为 `backend`，因此 `from app...` 导入能够正常解析。

## 3. 配置 Node.js 与 npm

进入 `Settings -> Languages & Frameworks -> Node.js`：

- Node interpreter：选择本机 Node.js
- Package manager：选择 Node 安装目录中的 `npm`

在 PyCharm Terminal 中安装前端依赖：

```powershell
cd frontend
cmd /c npm install
```

## 4. PyCharm 运行配置

项目 `.run` 目录提供了四个共享配置：

| 配置 | 用途 |
|---|---|
| CasaMind Backend Debug | 不启用 reload，适合 Python 断点调试 |
| CasaMind Backend Dev | 启用 Uvicorn reload，适合日常编码 |
| CasaMind Frontend | 启动 Vite 开发服务器 |
| CasaMind Full Stack | 同时启动后端 Debug 和前端 |

首次打开项目后，在 PyCharm 右上角运行配置列表选择 `CasaMind Full Stack`，点击 Run。需要后端断点时点击 Debug。

访问地址：

```text
前端：http://127.0.0.1:5173
Swagger：http://127.0.0.1:8000/docs
OpenAPI：http://127.0.0.1:8000/openapi.json
```

## 5. 后端断点调试

建议在以下位置设置断点：

```text
backend/app/api/router.py
backend/app/services/planning.py
backend/app/ai/workflow.py
```

## 6. Graph RAG 首次验证

先启动 Chroma 和 Neo4j：

```powershell
docker compose up -d chroma neo4j
```

启动 FastAPI Debug 后，在 Yaak 依次发送：

```http
GET http://127.0.0.1:8000/api/v1/ai/status
Authorization: Bearer <access_token>
```

状态中应看到 `chroma=connected`、`neo4j=connected`、`langgraph=compiled`。首次使用发送：

```http
POST http://127.0.0.1:8000/api/v1/knowledge/bootstrap
Authorization: Bearer <access_token>
```

再发送：

```http
POST http://127.0.0.1:8000/api/v1/knowledge/search
Content-Type: application/json
Authorization: Bearer <access_token>

{
  "query": "孩子不吃辣，周三要快手，推荐什么晚餐？",
  "top_k": 4
}
```

响应中的 `vector_hits` 来自 Chroma，`graph_hits` 来自 Neo4j。最后请求 `POST /api/v1/plans/generate-weekly`，在 Agent Trace 页面可以看到双路检索节点和四个并行领域 Agent。

如果修改 `workflow.py` 后已有 Debug 进程仍显示 7 个节点，点击 PyCharm 的红色停止按钮，再重新点击 Debug；旧的 Uvicorn reload 子进程可能没有重新加载编译后的 LangGraph 图。最新工作流应显示 11 个节点，其中包括 4 个并行领域 Agent。

使用 `CasaMind Backend Debug`。不要在断点调试配置中添加 `--reload`，因为 reload 会创建子进程，可能导致断点命中不稳定。

推荐调试链路：

```text
POST /plans/generate-weekly
  -> api.router.generate_weekly
  -> PlanningService.generate
  -> HouseholdPlanningWorkflow.run
  -> _record Agent 节点
  -> PlanningResponse
```

## 6. 前端调试

先运行 `CasaMind Frontend`，然后访问 `http://127.0.0.1:5173`。

可在 PyCharm 中创建 `JavaScript Debug` 配置：

```text
Name: CasaMind Browser Debug
URL: http://127.0.0.1:5173
Browser: Chrome
```

前端关键断点位置：

```text
frontend/src/api.ts
frontend/src/views/PlannerView.vue
frontend/src/composables/useResource.ts
```

## 7. Docker Desktop 部署

确认 Docker Desktop 已启动。在 PyCharm 中进入：

```text
Settings -> Build, Execution, Deployment -> Docker
```

添加 `Docker for Windows` 连接。连接成功后，可在 PyCharm Services 窗口查看容器。

如果 PyCharm Terminal 提示无法识别 `docker`，先重启 PyCharm以刷新安装后的环境变量。如果仍不可用，将下面目录加入 Windows 系统 `PATH`：

```text
C:\Program Files\Docker\Docker\resources\bin
```

也可以临时使用完整路径验证：

```powershell
& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' version
```

完整部署：

```powershell
docker compose up -d --build
docker compose ps
```

该命令会启动：

- CasaMind frontend：`http://127.0.0.1:5173`
- CasaMind backend：`http://127.0.0.1:8000`
- PostgreSQL：`localhost:5433`（容器内仍为 5432）
- Redis：`localhost:6379`
- Neo4j Browser：`http://localhost:7474`
- Chroma：`http://localhost:8001`

查看日志：

```powershell
docker compose logs -f backend frontend
```

停止服务：

```powershell
docker compose down
```

## 8. Yaak 环境

创建 Workspace：`CasaMind API`。

创建环境变量：

```text
base_url = http://127.0.0.1:8000/api/v1
run_id =
```

也可以使用 `http://127.0.0.1:8000/openapi.json` 导入 FastAPI OpenAPI 文档。

## 9. Yaak 推荐测试流程

### 9.1 健康检查

```http
GET {{base_url}}/health
```

预期 `200`。

### 9.2 查询家庭上下文

依次执行：

```http
GET {{base_url}}/dashboard
GET {{base_url}}/members
GET {{base_url}}/calendar/events
GET {{base_url}}/tasks
GET {{base_url}}/meals
GET {{base_url}}/shopping
GET {{base_url}}/knowledge
```

### 9.3 生成周计划

```http
POST {{base_url}}/plans/generate-weekly
Content-Type: application/json
Authorization: Bearer {{access_token}}
```

```json
{
  "prompt": "我们家3口人，下周预算500元，孩子不吃辣，周三晚上有绘画课，请安排一周晚餐、采购和家务。",
  "budget": 500
}
```

预期 `201`。将响应中的 `run_id` 填入 Yaak 环境变量 `run_id`。

### 9.4 查询 Agent Trace

```http
GET {{base_url}}/agents/runs/{{run_id}}
```

检查 Intent、Graph Retriever、Vector Retriever、Coordinator、四个领域 Agent、Planning、Verifier 和 Final Planner 节点。

### 9.5 保存计划

```http
POST {{base_url}}/plans/{{run_id}}/confirm
```

预期 `200`，响应状态为 `confirmed`。

## 10. 联调排错顺序

1. 浏览器请求失败时先检查 PyCharm 后端控制台。
2. 在 Yaak 调用 `/health`，确认 API 服务存活。
3. 检查前端请求是否为 `/api/v1/...`。
4. 检查端口 `5173`、`8000` 是否被其他进程占用。
5. Docker 模式使用容器日志，开发模式使用 PyCharm Run/Debug 控制台。
6. Demo 模式的 Agent Run 存在内存中，重启后端后旧 `run_id` 会失效。

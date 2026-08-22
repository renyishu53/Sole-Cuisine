# CasaMind / SoloChef 项目长期记忆

## 项目定位（2026-08-07 锁定）

- 产品名：**SoloChef — AI 独居膳食与采买规划师**（原 HomePilot 家庭综合事务规划师，已收敛）。
- 目标用户：独居自炊人群，细分 增肌/减脂/健康维护 三类，兼顾性别差异。
- 核心闭环：营养目标（TDEE+宏量）→ AI 每日三餐 → 购物清单 → 食材替换联动 → 执行反馈学习。
- 差异化：营养目标驱动的约束式生成（非标签式）+ 执行反馈闭环。
- 明确不做：家庭协同、任务看板、完整日历、通用知识库问答、库存、通知、外部同步、商超价格比对、剩余食材利用。
- 降级可选：女性生理周期（非医疗化）、预算（采购成本面）。

## 技术选型

- 主库：**MySQL**（求职认知度优先；已处理 dialect 适配——无 postgresql_where/数组/Postgres-only server_default，JSON 列用 `default=list` 兼容）。Neo4j + Chroma 保留。
- 现状（2026-08-09）：去家庭化 + MySQL 适配 + Phase 3 深化均已完成。`config.py` 默认 `mysql+aiomysql://...solochef`；`docker-compose` 用 mysql:8.0；alembic 0001(初始)+0002(删6遗留表) 迁移。本地 venv（uv 管理）未装 aiomysql，真实运行走 docker-compose；本地零配置可设 `DATABASE_URL=sqlite+aiosqlite:///./solochef.db`。**注意**：`db/session.py` 已按 SQLite 方言区分连接池参数（pool_size/max_overflow/pool_timeout 仅非 SQLite 传入），测试用 SQLite 内存库可直接跑。

## 本地 venv 重建标准流程（2026-08-12 锁定）

**venv 损坏现象**：PyCharm 报 `Cannot run program '...backend/.venv/Scripts/python.exe': CreateProcess error=2`，根因是 `.venv/Scripts/` 和 `.venv/pyvenv.cfg` 缺失（可能由中断的 pip install 留下半成品）。修复固定流程：

```bash
cd backend
uv venv --allow-existing --python 3.12            # 用系统 Python 3.12.4 重建 venv 框架（会清空旧 site-packages）
uv sync --no-install-project --extra ai --extra dev   # 必须加 --no-install-project，否则 sandbox safe-delete 拦截 build
uv pip install --reinstall pyyaml jsonpatch six chardet charset_normalizer   # chromadb/langchain/celery 隐式依赖
```

**约束**：
- ❌ 不能用 `uv sync`（不带 `--no-install-project`）——会 build `casamind-api` 触发 sandbox `SAFE_DELETE_FAIL_CLOSED`
- ❌ 不能用 `uv run`——同根因
- ❌ `uv pip install <pkg>` 不带 `--reinstall` 时若 uv 认为已满足会跳过实际写入（dist-info 在但模块目录缺）
- ✅ 启动命令：`cd backend && DATABASE_URL="sqlite+aiosqlite:///./solochef.db" PYTHONPATH=. .venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
- ✅ PyCharm Run 配置在 `.run/CasaMind Backend Dev.run.xml`：SDK=$PROJECT_DIR$/backend/.venv/Scripts/python.exe，MODULE_MODE=true，SCRIPT=uvicorn，PARAMS=`app.main:app --reload --host 127.0.0.1 --port 8000`，ENV_FILES=$PROJECT_DIR$/.env。venv 重建后 PyCharm 需 File → Invalidate Caches → Restart 或重新指定 Interpreter。
- 表结构现状：**14 张**以 `user_id` 为核心的聚焦表（Phase 3 清理 6 张遗留表：calendar_events/calendar_event_exceptions/plan_tasks/plan_budgets/task_completions/inventory_items）。
- 架构决策：**去家庭化**（非"保留 family 抽象"）——删除 families/memberships/profiles/invitations，所有业务表 family_id→user_id，与 MySQL 迁移一起一次到位。已写入 PRD 5.5/6.1/9/10。

## 文档

- PRD 文件名仍为 `HomePilot_PRD.md`，内容已于 2026-08-07 重写为 SoloChef 定位（含第 10 节收敛说明）。
- `SoloChef 项目分析报告.md` 已更新至 v9.0（2026-08-09）：Phase 1/2/3 后端开发流程全部完成，pytest 54 passed / ruff 全绿 / mypy 54 源文件无问题。附录表清单 14 张，API 87 端点。
- `CasaMind 项目分析报告.md` 顶部「〇」章节已于 2026-08-07 同步为 SoloChef 当前状态（收敛要点 + 后端去家庭化迁移进展 + MySQL 适配 + 前端原型 + 验证 + 状态结论表）；第二至八章保留为定位收敛前 CasaMind 家庭导向历史基线，供迁移对照。

## 后端开发流程（2026-08-09 三阶段全部完成）

- **Phase 1 营养闭环（P0）✅**：`.env` MySQL 同步 / 画像+营养目标 API（Mifflin-St Jeor TDEE）/ Verifier 营养达成率校验 [90%,110%]。
- **Phase 2 技术债务清理（P1）✅**：工作流 13→11 节点（移除 task/calendar）/ checkpoint 迁 InMemorySaver（移除 PostgreSQL 依赖）/ 种子库+Demo 数据换血独居膳食向。
- **Phase 3 深化（P2）✅**：遗留表清理 20→14 表（alembic 0002）/ 食材营养库扩充至 105 种外置 JSON（校准标注）/ git 仓库 `main` 分支 + GitHub Actions CI 四道门禁（ruff/mypy/pytest/alembic）。
- **工作流现状**：11 节点 StateGraph，3 个领域智能体（meal/shopping/budget）。
- **去家庭残留（2026-08-10，commit 5d96133）✅**：evaluation.py/verifier/meal agent 忌口约束改读单人 `UserProfile.constraints`（原 `MemberProfile` + members 恒空导致校验失效）；三个 agent 签名 members/events 收口为可选默认空；删 `_empty_calendar_result()` 垫片 + `PlanningResponse.calendar` 字段 + 孤儿 `calendar_planning.py`。`draft.tasks` 保留（前端在用）。members/CalendarEvent 仍在 knowledge/graph_store/conversation 服务签名中作为可选空参（更广残留面未动）。
- **RAG 真实端到端验证（2026-08-10，commit 96a772e）✅**：起 docker-compose（redis/neo4j/chroma；mysql:8.0 未缓存且离线无法拉取，但 RAG 检索不依赖 SQL）跑 `backend/scripts/rag_smoke_test.py`，真实 Chroma+Neo4j 底座下 `knowledge.retrieve()` 返回 chroma_status=connected + vector_hits>0（语义命中）+ neo4j_status=connected。**暴露并修复了被单元测试 stub 漏掉的真实 bug**：`embeddings.py::SentenceTransformerEmbedding` 与 Chroma 1.5.9 接口不兼容（缺 embed_query/embed_documents、embed_query 返回类型错），已改为返回 model.encode 原始 numpy 输出 + 补 is_legacy=False。reranker（bge-reranker-v2-m3）本地无权重且离线无法下载 → 优雅降级 rerank_status=disabled（可选增强，非核心检索）。
- **剩余可选深化**：G07(食材替换营养联动)/G08(购物替代图谱化)/G11(前端复盘页)/G14(MySQL集成测试)/G15(前端测试体系)。

## 备餐规划"局部修改"机制（2026-08-12 阶段一 ✅）

新增 `PlanReviseService` 支持"自然语言修改要求 → LLM 解析为结构化 ReviseOperation（7 种 operation）→ 局部修改 + 联动计算 → before/after diff 预览 → 确认派生新版本"。修改对话复用 `ChatSession`/`ChatMessage` 持久化完整历史。

**新增**：`app/schemas/plan_revise.py`、`app/services/plan_revise.py`、`tests/test_plan_revise.py`（12 测试全过）
**修改**：`app/repositories/planning.py` 加 `derive_plan_with_modifications`、`app/repositories/conversations.py` 加 `get_message`、`app/api/router.py` 加 `POST /plans/{plan_id}/revise` + `POST /plans/{plan_id}/revise/{revise_id}/confirm`、`tests/conftest.py` 测试时强制 `plan_revise_service._llm_model = None` 走 demo 兜底

**关键设计**：
1. 预览/提交分离 — revise 只生成预览存到 ChatMessage.payload，confirm 才调 derive_plan_with_modifications 落库
2. 修改后子项直接存消息 payload，confirm 取出直接落库不重复 LLM 调用
3. 对话会话按 `[计划v{plan_id}]` 标题前缀复用，多轮修改保留完整历史
4. `_find_meal_index` 三级匹配：day+meal_type 精确 → 该天唯一餐 → 该天第一餐兜底
5. `MealProposal.meal_type` 合并进 tags 持久化（PlanMealItem 无 meal_type 字段）
6. `adjust_macro_target` 仅记录意图，不直接改 NutritionGoal

**待办阶段二**：前端三栏布局 PlannerView 重写 / AI 对话意图分流 / 图片识别

## 反馈闭环（已实现，前轮）

- `plan_feedback` 偏差表 + 回图谱/向量 + 补偿重放 + 前端 TasksView/ShoppingView/MealsView 反馈 UI。
- 餐食 Agent 口味学习（taste_profile 注入 meal() + 前端口味画像面板）。

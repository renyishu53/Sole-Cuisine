# 本轮工作概览：Graph RAG 2.1 未完成项 — BGE-M3 语义向量接入

## 完成内容

**后端（沿用现有代码风格：全类型标注、懒初始化、线程隔离、优雅降级）**
- 新建 `backend/app/services/embeddings.py`：嵌入后端工厂 + Chroma 兼容适配器；BGE-M3 仅以 `local_files_only=True` 加载，依赖/模型缺失自动回退内置 ONNX MiniLM 并输出降级日志。
- `backend/app/core/config.py`：新增 `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_MODEL_PATH` / `EMBEDDING_DEVICE` 四项配置。
- `backend/app/services/vector_store.py`：嵌入函数改由工厂解析（线程内懒加载）；BGE-M3（1024 维）激活时集合自动切换为 `casamind_knowledge-bge-m3`，与内置 384 维集合隔离，避免维度冲突。
- `backend/app/services/knowledge.py` + `schemas/domain.py`：检索诊断 `diagnostics.embedding` 与 `GET /ai/status` 新增 `embedding` 字段（脱敏文案）。
- `backend/pyproject.toml`：新增 `bge` optional extra（`sentence-transformers>=3.0,<6.0`）；`.env.example` 补充对应变量。

**前端**
- `types.ts`：`AIServiceStatus` 增加 `embedding` 字段。
- `KnowledgeView.vue`：基础设施状态条新增"语义模型"指示（本地语义模型 BGE-M3 / 内置轻量语义模型）。

**测试与验证**
- `tests/test_rag.py` 新增 5 项嵌入后端测试（stub 模块模拟，无需下载真实模型）。
- `pytest`：47 passed（原 42 + 新 5）；前端 `npm run build` 通过；前端开发服务 5173 HTTP 200。
- 冒烟：无 BGE-M3 环境下正确回退内置模型，集合名保持 `casamind_knowledge`。

**报告**
- 《CasaMind 项目分析报告.md》2.1 节：BGE-M3 移入已完成，附模型本地下载三选一步骤与启用流程；综合结论 94% → 95%；第四节缺口、第五节验证结果（42→47）、依赖表同步更新。

## 待用户操作
- BGE-M3 模型约 2.2GB，需自行下载（步骤见报告 2.1 节：hf-mirror / ModelScope / git-lfs）。
- 下载后安装 `pip install "casamind-api[bge]"`，`.env` 设置 `EMBEDDING_PROVIDER=bge-m3` 和 `EMBEDDING_MODEL_PATH`，重启后端并重新"初始化知识"。

## 续：真机部署与验证（2026-08-05，用户已下载模型后）
- 模型已下载至 `D:\software\tools\modes\bge-m3`（ModelScope，文件完整）。
- 依赖已装：`torch 2.13.0+cpu`（CPU 版，116MB）+ `sentence-transformers 5.6.1`（经 `uv pip install`，连带 transformers/scipy/scikit-learn；tokenizers 0.23.1→0.22.2）。
- `.env` 已配 `EMBEDDING_PROVIDER=bge-m3` / `EMBEDDING_MODEL_PATH=D:\software\tools\modes\bge-m3` / `EMBEDDING_DEVICE=cpu`。
- 真机验证通过：嵌入后端解析为 `本地语义模型 BGE-M3`（is_bge_m3=True），`encode` 产出 1024 维；真 Chroma 容器已创建 `casamind_knowledge-bge-m3` 集合（count=0 待灌入），心跳正常；pytest 47 passed。
- 小修：`knowledge.py` status() 的 `collection` 字段改为返回真实集合名（`collection_name_for(embedding)`），前端显示与实际一致。
- 后端以 `--reload` 模式运行于 127.0.0.1:8000。
- 待用户 UI 确认：登录后知识库页"语义模型"显示"本地语义模型 BGE-M3"，点"初始化知识"将真实家庭文档以 1024 维灌入新集合。

## 续2：用户截图反馈 4 个体验问题根因修复（同日夜间）
- **登录方式**：端到端验证两条端点（密码/SMS）都活，前端 UI 已有分段 tab，无 bug
- **仪表盘真实化**：`router.py:149` dashboard 改用 `context.display_name` + 实时小时/日期/真实预算/任务计算 greeting/date_label/notices/tasks/budget/week_progress；无活跃周计划时给占位；Redis 缓存键改 `dashboard:{family_id}:{user_id}`；新增 `RuntimeStateService.delete_prefix`（SCAN+DEL）批量失效；pytest 47 passed；真机验证 uid=5（小张）返 `下午好，小张 / 小张的家 / 8 月 5 日 · 周三 / 真实预算提醒`
- **路由空白页**：`router.ts` 加 `router.onError` 监听 chunk 加载失败自动 `window.location.assign(to.fullPath)` 重载，`chunkReloadKey` 去重
- **BGE-M3 状态显示**：根因是 `--reload` 模式下 worker 进程 `vector_store._embedding` lru_cache 不自动失效；改为干净 uvicorn 启动后 `/ai/status` 立即返回正确状态；已用 API 触发 bootstrap 把 3 份引导文档灌入 `casamind_knowledge-bge-m3`
- 前端 `npm run build` 通过（23.89s），前端 5173 与后端 8000 在线

## 续3：用户再次反馈登录与路由切换问题（2026-08-06）
- **登录双模式**：前端 `AuthView.vue` 已具备「密码登录」/「短信验证码」分段 tab，对应后端 `/auth/login`（手机号+密码）与 `/auth/sms/login`（手机号+验证码）两条端点；界面与用户提供截图一致，无需额外改动。
- **路由切换仍需手动刷新（根因定位）**：控制台警告 `[Vue warn]: Component inside <Transition> renders non-element root node that cannot be animated` 指向 `CalendarView`。`AppShell.vue` 用 `<Transition mode="out-in">` 包裹 `<RouterView>`，而 Vue `<Transition>` 要求子组件必须是**单一根元素**才能播放入场/离场动画。`CalendarView` 与 `MembersView` 的 `<template>` 都是「`<AsyncState>` + `<Teleport to="body">」双根节点**，导致 out-in 模式下旧组件离场钩子不触发、新组件一直挂不上 → 页面空白，只能手动刷新。
  - 修复：`CalendarView.vue` / `MembersView.vue` 模板外层各加单一根 `<div class="page-view" style="display: contents">` 包裹，满足 Vue 单根约束（`display:contents` 不额外生成布局盒子，不影响视觉排版）。
  - 加固（保留）：`useResource.ts` 增 `watch(route.fullPath, load)`；`AppShell.vue` key 改 `route.fullPath`；`router.ts` chunk 错误兜底正则增 `Unexpected token '<'`。
  - 其余控制台信息均为噪音：`chrome-extension://` 是浏览器插件报错，与项目无关；`401 Unauthorized` 是刷新后 session 丢失被重定向到 `/login`，非切换问题。
- 前端 `npm run build` 通过（20.79s）。

## 2.1 剩余缺口（下一轮）
rerank（bge-reranker-v2-m3）、LLM/NER 级实体关系抽取、复杂 Cypher 查询改写、检索评测集及 Recall/nDCG 报告。

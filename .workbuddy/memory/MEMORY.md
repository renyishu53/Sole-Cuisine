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
- 现状（2026-08-07）：去家庭化 + MySQL 适配已完成。`config.py` 默认 `mysql+aiomysql://...solochef`；`docker-compose` 用 mysql:8.0；新增 dialect-agnostic 初始迁移 + 启动幂等建表。本地 venv（uv 管理）未装 aiomysql，真实运行走 docker-compose；本地零配置可设 `DATABASE_URL=sqlite+aiosqlite:///./solochef.db`。
- 表结构现状：20 张以 `user_id` 为核心的聚焦表（删 5 家庭表、增 user_profiles/nutrition_goals）。
- 架构决策：**去家庭化**（非"保留 family 抽象"）——删除 families/memberships/profiles/invitations，所有业务表 family_id→user_id，与 MySQL 迁移一起一次到位。已写入 PRD 5.5/6.1/9/10。

## 文档

- PRD 文件名仍为 `HomePilot_PRD.md`，内容已于 2026-08-07 重写为 SoloChef 定位（含第 10 节收敛说明）。
- `CasaMind 项目分析报告.md` 顶部「〇」章节已于 2026-08-07 同步为 SoloChef 当前状态（收敛要点 + 后端去家庭化迁移进展 + MySQL 适配 + 前端原型 + 验证 + 状态结论表）；第二至八章保留为定位收敛前 CasaMind 家庭导向历史基线，供迁移对照。

## 反馈闭环（已实现，前轮）

- `plan_feedback` 偏差表 + 回图谱/向量 + 补偿重放 + 前端 TasksView/ShoppingView/MealsView 反馈 UI。
- 餐食 Agent 口味学习（taste_profile 注入 meal() + 前端口味画像面板）。

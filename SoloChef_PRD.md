# SoloChef - AI 个人目标营养备餐助手 PRD

> 更新日期：2026-08-07  
> 本文档由原 HomePilot/CasaMind 家庭事务规划方向收敛而来。当前项目定位调整为 SoloChef：面向独居且偏好自主烹饪的人群，围绕“身体数据 -> 营养目标 -> 三餐计划 -> 购物清单 -> 执行反馈”的高频闭环构建 AI 应用。  
> 当前代码已部分完成去家庭化：后端主库配置、初始迁移和部分模型已转向 MySQL + user_id 单用户隔离；前端路由、部分 schema、AI workflow 中仍存在家庭/日历/任务等遗留语义，后续应按本 PRD 继续收敛。

## 1. 产品定位

### 1.1 一句话定位

SoloChef 是一款面向独居自炊用户的 AI 个人目标营养备餐助手，帮助用户按增肌、减脂或健康维护目标生成每日三餐、精确购物清单，并通过执行反馈持续学习个人口味、预算和营养达标情况。

### 1.2 目标用户

| 用户群体 | 关键诉求 | 产品抓手 |
|---|---|---|
| 健身增肌人群 | 蛋白质达标、热量轻盈余、训练恢复 | 高蛋白食谱、宏量营养约束、蛋白来源优化 |
| 减脂塑形人群 | 控制热量、保持饱腹、减少外卖 | 热量赤字、低脂高蛋白、购物预算控制 |
| 健康维护人群 | 三餐均衡、食材多样、降低决策成本 | 均衡配比、低重复度、偏好学习 |

系统在营养目标计算中考虑性别、年龄、身高、体重、活动量差异。女性生理周期饮食标记作为 P2 可选能力，必须自愿开启、非医疗化、可随时关闭。

### 1.3 明确不做

为避免“综合事务规划师”过宽导致业务边界模糊，MVP 不做：

- 家庭空间、家庭成员、邀请协作、成员权限
- 家务任务、完整日历、外部日历同步、通知中心
- 通用知识库问答、生活百科、维修清洁育儿等宽泛知识
- 库存管理、剩余食材利用、真实电商下单
- 附近商超实时价格比对

购物成本在 MVP 中采用“历史均价 + 手动实际花费核销”的方式估算，不依赖不稳定外部价格数据。

## 2. 核心价值

| 问题 | 现状 | SoloChef 方案 |
|---|---|---|
| 每天不知道吃什么 | 靠灵感或外卖，重复且营养不稳定 | 基于目标热量、宏量营养、偏好和忌口生成三餐 |
| 增肌/减脂目标难执行 | 只知道大概热量，难以落到每餐 | 计算 BMR/TDEE 和蛋白质、碳水、脂肪目标，并验证达标 |
| 买菜容易漏买或浪费 | 食谱和购物清单割裂 | 从食谱自动合并食材、按类别生成精确用量 |
| 换食材后营养失衡 | 替换后不知道热量和蛋白是否达标 | 替换后重算营养，并同步购物清单 |
| AI 计划一次性、不可沉淀 | 每次都像从零开始 | 反馈口味、预算、达标率，回流 Graph RAG 和向量记忆 |

## 3. 核心业务流程

```text
用户注册/登录
  -> 填写身体数据、目标、偏好、忌口、预算
  -> Nutrition Agent 计算 BMR/TDEE 与宏量营养目标
  -> 用户输入自然语言需求或选择每日/每周计划
  -> Graph Retriever 查询用户-食材-菜谱-偏好关系
  -> Vector Retriever 查询菜谱与营养知识
  -> Meal Agent 生成三餐候选
  -> Shopping Agent 合并食材并估算预算
  -> Verifier Agent 校验热量、宏量、忌口、预算
  -> 用户查看、替换、保存计划
  -> 执行阶段记录口味评分、实际花费、购买状态、达标偏差
  -> Feedback Agent 写回偏好画像、知识图谱和向量记忆
```

该流程必须避免“输入一句话 -> LLM 线性生成一份计划 -> 结束”的玩具化问题。产品差异点在于约束计算、计划校验、购物联动和反馈学习。

## 4. 功能需求

### F1. 用户画像与营养目标 P0

- 采集身高、体重、年龄、性别、活动量。
- 支持目标类型：增肌、减脂、健康维护。
- 采集饮食偏好、忌口、过敏、预算上限。
- 使用 Mifflin-St Jeor 公式计算 BMR，并按活动量计算 TDEE。
- 按目标类型生成目标热量、蛋白质、碳水、脂肪建议。
- 支持用户手动微调目标。

验收标准：

- 身体数据变更后可重新计算目标。
- AI 规划必须读取当前营养目标作为硬约束。
- 目标结果展示计算依据和非医疗免责声明。

### F2. AI 每日三餐计划 P0

- 根据营养目标、忌口、偏好生成早餐、午餐、晚餐。
- 每餐展示菜名、主要食材、克重、热量、蛋白质、碳水、脂肪。
- 支持“高蛋白”“低脂”“快手”“低预算”等标签。
- 支持单餐重生成和单个食材替换。
- 展示全天目标达标进度。

验收标准：

- 输出不能包含用户明确忌口或过敏食材。
- 全天热量和宏量营养应接近目标区间。
- 每个计划必须有 Verifier 校验结果。

### F3. 购物清单与预算 P0

- 从已保存食谱自动生成购物清单。
- 合并重复食材，按蔬菜、肉蛋奶、主食、豆制品、水果、调味品等分类。
- 按食谱克重计算用量。
- 基于历史均价估算总价。
- 支持勾选已购买、记录实际价格、统计预算偏差。

验收标准：

- 食谱替换后购物清单同步更新。
- 超预算时给出替代食材或餐品建议。
- 实际花费写入反馈，用于下次预算估算。

### F4. 食材替换与营养联动 P0

- 用户可替换某道菜或某个食材。
- 替换后自动重算单餐和全天营养。
- 若替换导致目标不达标，给出补偿建议。
- 同步更新购物清单相关条目。

验收标准：

- 替换前后营养差异可视化。
- 清单增删改准确反映替换结果。
- 替换记录进入偏好学习。

### F5. Graph RAG 饮食知识 P0

Neo4j 存储结构化关系：

```text
User -[:HAS_GOAL]-> NutritionGoal
User -[:LIKES]-> Recipe
User -[:AVOIDS]-> Ingredient
Recipe -[:REQUIRES]-> Ingredient
Ingredient -[:HAS_NUTRIENT]-> Nutrient
Ingredient -[:SUBSTITUTE_FOR]-> Ingredient
Recipe -[:HAS_TAG]-> Tag
Feedback -[:ABOUT]-> Recipe
```

Milvus 存储语义知识：

- 菜谱 chunk
- 食材营养 chunk
- 历史计划摘要
- 用户反馈摘要
- 对话摘要

验收标准：

- 计划生成前可检索偏好、忌口、替代关系。
- 推荐结果能展示引用来源。
- 用户反馈能沉淀到图谱或向量记忆。

### F6. AI Agent 轨迹 P1

- 展示 LangGraph 节点执行过程。
- 展示检索来源、营养校验和预算校验。
- 保存 Agent run，支持调试和作品集展示。

验收标准：

- 用户能看到“为什么这样推荐”。
- 开发者能定位失败节点、错误信息和输入输出摘要。

### F7. 执行反馈与复盘 P0

- 餐后记录喜欢/不喜欢、是否吃完、替换原因。
- 购物后记录实际花费和未买到食材。
- 汇总每日/每周热量、蛋白质、预算达标率。
- 将反馈回流到偏好画像、Neo4j 和 Milvus。

验收标准：

- 下次生成能体现反馈结果。
- 支持反馈同步失败后的补偿重放。
- 复盘结果能指出主要偏差来源。

### F8. 女性周期饮食标记 P2

- 自愿开启，默认关闭。
- 仅作为饮食倾向微调，不提供医疗诊断或治疗建议。
- 只记录必要阶段标签，不采集过度敏感信息。

## 5. MVP 范围

P0 必做：

- 用户认证
- 用户画像与营养目标
- 每日三餐 AI 生成
- 购物清单生成与核销
- 食材替换与营养重算
- 执行反馈与偏好学习
- Milvus 菜谱/营养知识库
- Neo4j 饮食关系图谱
- LangGraph 多智能体规划流

P1 增强：

- Agent 轨迹可视化
- 每周营养复盘
- 菜谱知识库管理
- 计划版本管理

P2 可选：

- 女性周期饮食标记
- 更细的训练日/休息日营养策略
- 价格数据导入，但不做实时商超比价

## 6. 技术方案

### 6.1 前端

```text
Vue 3
TypeScript
Vite
Vue Router
Pinia
Element Plus
Axios
ECharts
SCSS
lucide-vue-next
pnpm
```

核心页面：

- 今日营养
- AI 备餐规划
- 三餐计划
- 购物清单
- 反馈复盘
- 知识库
- Agent 轨迹
- 个人设置

### 6.2 后端

```text
FastAPI
Python 3.11+
SQLAlchemy 2.x
Pydantic v2
Alembic
Celery
Redis
Loguru
Pytest
```

后端分层：

```text
api             # 路由与鉴权边界
schemas         # Pydantic DTO
models          # SQLAlchemy 模型
repositories    # 数据访问
services        # 业务编排
ai              # LangGraph / Agent / RAG / Prompt
worker          # Celery 异步任务
core            # 配置、安全、日志
db              # MySQL / Redis / Neo4j / Milvus 连接
```

### 6.3 数据库与中间件

| 技术 | 用途 | 结论 |
|---|---|---|
| MySQL 8 | 用户、画像、营养目标、计划、购物、反馈、Agent run | 主业务库，当前配置和 Docker Compose 已指向 MySQL |
| Neo4j 5 Community | 用户偏好、食材、菜谱、营养、替代关系 | Graph RAG 核心 |
| Milvus | 菜谱、营养知识、计划摘要、反馈摘要向量检索 | RAG 语义检索 |
| Redis | 缓存、Celery broker、运行状态 | 基础设施 |

数据库选型结论：

- 个人项目不需要为了“企业级感”强行使用 PostgreSQL。
- MySQL 更贴近常见招聘认知和本地部署习惯，足以支撑当前关系数据。
- 项目已有 MySQL 配置，应继续清理 PostgreSQL 遗留描述和 dialect 差异。
- 注意 MySQL 的 JSON、索引、字符集、严格模式和布尔默认值兼容性。

## 7. 目标数据表

目标 MySQL 业务表控制在约 14 张：

```text
users
refresh_sessions
user_profiles
nutrition_goals
daily_meal_plans
meal_items
recipes
shopping_lists
shopping_items
expense_records
plan_feedback
chat_sessions
chat_messages
agent_runs
```

设计原则：

- 所有个人业务数据以 user_id 隔离。
- 不保留 families、family_memberships、family_invitations。
- 不用 family 抽象伪装单用户模型。
- 旧的 calendar_events、plan_tasks、plan_budgets、inventory_items 若不服务核心闭环，应迁移为反馈/计划字段或删除。

## 8. API 草案

```text
POST /api/auth/login
POST /api/auth/register
GET  /api/profile
PUT  /api/profile
POST /api/profile/nutrition-goal
POST /api/meals/generate-daily
POST /api/meals/{id}/replace
GET  /api/meals/today
POST /api/shopping/generate
PATCH /api/shopping/items/{id}
POST /api/shopping/items/{id}/verify
POST /api/feedback
GET  /api/feedback/overview
POST /api/chat/planning
GET  /api/agents/runs/{id}
POST /api/knowledge/upload
POST /api/knowledge/rebuild-index
```

## 9. 非功能需求

| 分类 | 要求 |
|---|---|
| 性能 | 普通 API 响应 < 500ms；AI 首 token < 3s |
| 可用性 | LLM 不可用时返回兜底提示，基础 CRUD 不受影响 |
| 安全 | JWT 鉴权，user_id 数据隔离 |
| 隐私 | 身体数据和周期标记最小化采集，默认不外传 |
| 可观测 | 请求日志、Agent trace、失败节点、重试记录 |
| 可维护 | Repository 只处理数据访问，Service 承载业务规则，AI 工作流独立 |
| 可测试 | 营养计算、购物合并、替换重算、权限隔离必须有单元测试 |

## 10. 当前代码同步结论

已对齐：

- 项目配置默认 `SoloChef API`。
- `docker-compose.yml` 使用 MySQL、Redis、Neo4j、Milvus。
- Alembic 初始迁移命名为 `0001_initial_solochef`。
- 部分 SQLAlchemy 模型已使用 `user_id`，并新增 `UserProfile`、`NutritionGoal`。

仍需收敛：

- 前端 `router.ts`、`AppShell.vue` 和部分页面仍保留 CasaMind、家庭成员、日历、家务、预算等旧入口。
- AI workflow 仍命名为 `HouseholdPlanningWorkflow`，意图和 trace 文案仍偏家庭规划。
- schema 和 domain agent 中仍存在 FamilyMember、Task assignee、家庭营养等旧概念。
- README 和旧 UI 文档需要同步 SoloChef 定位。

后续工程优先级：

1. 前端导航和页面信息架构改为 SoloChef。
2. 后端 schema/service/workflow 彻底去 Family 命名。
3. 删除或降级日历、家务、库存模块。
4. 补齐营养计算、购物清单合并、食材替换重算测试。

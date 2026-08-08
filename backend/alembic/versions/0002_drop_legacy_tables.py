"""drop legacy family-oriented tables

Revision ID: 0002_drop_legacy_tables
Revises: 0001_initial_solochef
Create Date: 2026-08-09

Phase 3 清理：删除 6 张去家庭化后不再使用的遗留表。对应 SQLAlchemy 模型已在
``app/models/identity.py`` 中移除：

  - ``calendar_events`` / ``calendar_event_exceptions``（日程与周期例外）
  - ``plan_tasks`` / ``plan_budgets`` / ``task_completions``（任务、预算明细、任务完成记录）
  - ``inventory_items``（家庭库存）

使用 ``DROP TABLE IF EXISTS`` 兼容两种环境：

  - 已运行旧 0001（模型仍存在时）的库：这些表存在，将被删除；
  - 全新库：0001 现按精简后的 metadata 建表，这些表本就不存在，``IF EXISTS`` 使其安全跳过。

SQLite / MySQL / PostgreSQL 均支持 ``DROP TABLE IF EXISTS``。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_drop_legacy_tables"
down_revision: None | str = "0001_initial_solochef"
branch_labels: None | str = None
depends_on: None | str = None

# 按外键依赖逆序删除：先删子表再删父表，避免外键约束阻断。
_LEGACY_TABLES: tuple[str, ...] = (
    "task_completions",  # FK -> plan_tasks
    "plan_tasks",  # FK -> weekly_plans
    "plan_budgets",  # FK -> weekly_plans
    "inventory_items",  # FK -> users
    "calendar_event_exceptions",  # FK -> calendar_events
    "calendar_events",  # FK -> users
)


def upgrade() -> None:
    for table_name in _LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table_name}")


def downgrade() -> None:
    # 这些表对应的模型已在 Phase 3 移除，无法通过 metadata 自动重建。
    # 如需回滚，须手动恢复模型类与历史迁移，此处保持空操作并记录原因。
    return None

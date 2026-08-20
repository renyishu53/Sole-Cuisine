"""add meal checkin fields

Revision ID: 0007_add_meal_checkin_fields
Revises: 0006_add_plan_conflict_details
Create Date: 2026-08-14

阶段4（采购 + AI 对话 + 打卡）：为 ``PlanMealItem`` 新增四列，记录餐食级别
"已吃"打卡与未吃偏差：

- ``eaten``（Boolean，是否已吃，默认 false）
- ``eaten_at``（DateTime，打卡时间，未吃时为 NULL）
- ``deviation_type``（String(40)，未吃偏差枚举：not_available/no_appetite/ate_other）
- ``deviation_reason``（String(500)，偏差原因自由文本）

历史餐食四列均为默认值（未吃、无偏差），向后兼容。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0007_add_meal_checkin_fields"
down_revision: str = "0006_add_plan_conflict_details"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    """判断列是否已存在（跨方言）。全新库经 0001 create_all 时这些列已在，需幂等跳过。"""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("plan_meal_items", "eaten"):
        op.add_column(
            "plan_meal_items",
            sa.Column("eaten", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column("plan_meal_items", "eaten_at"):
        op.add_column(
            "plan_meal_items",
            sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("plan_meal_items", "deviation_type"):
        op.add_column(
            "plan_meal_items",
            sa.Column("deviation_type", sa.String(40), nullable=True),
        )
    if not _has_column("plan_meal_items", "deviation_reason"):
        op.add_column(
            "plan_meal_items",
            sa.Column("deviation_reason", sa.String(500), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("plan_meal_items", "deviation_reason")
    op.drop_column("plan_meal_items", "deviation_type")
    op.drop_column("plan_meal_items", "eaten_at")
    op.drop_column("plan_meal_items", "eaten")

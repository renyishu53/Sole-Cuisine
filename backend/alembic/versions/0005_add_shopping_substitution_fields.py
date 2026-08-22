"""add shopping substitution confirmation fields

Revision ID: 0005_shopping_sub_fields
Revises: 0004_add_lifestyle_constraints
Create Date: 2026-08-14

阶段3（任务B 食材替换确认闭环）：为 ``PlanShoppingItem`` 新增两列——
``substituted_from``（被替换前的原食材名，String(120)）、
``substituted_accepted``（用户是否确认替换，Boolean，NULL=待确认）。

两列均可空，无服务端默认值，兼容历史购物项（从未被替换 → 均为 NULL）。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0005_shopping_sub_fields"
down_revision: str = "0004_add_lifestyle_constraints"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    """判断列是否已存在（跨方言）。全新库经 0001 create_all 时这些列已在，需幂等跳过。"""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("plan_shopping_items", "substituted_from"):
        op.add_column(
            "plan_shopping_items",
            sa.Column("substituted_from", sa.String(120), nullable=True),
        )
    if not _has_column("plan_shopping_items", "substituted_accepted"):
        op.add_column(
            "plan_shopping_items",
            sa.Column("substituted_accepted", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("plan_shopping_items", "substituted_accepted")
    op.drop_column("plan_shopping_items", "substituted_from")

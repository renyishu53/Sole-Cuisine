"""add nutrition goal range fields

Revision ID: 0003_add_nutrition_goal_range
Revises: 0002_drop_legacy_tables
Create Date: 2026-08-13

将营养目标从固定值改为范围值设计，新增 8 个列：
热量范围（calories_min / calories_max）、蛋白质范围（protein_min / protein_max）、
碳水范围（carb_min / carb_max）、脂肪范围（fat_min / fat_max）。

使用 ALTER TABLE ADD COLUMN 兼容 SQLite / MySQL / PostgreSQL。
新列均有默认值，确保历史数据迁移后立即可读。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_nutrition_goal_range"
down_revision: str = "0002_drop_legacy_tables"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    """判断列是否已存在（跨方言）。

    0001 用 ``Base.metadata.create_all`` 按当前元数据建表，全新库在走到本迁移时
    这些列其实已经存在；加此保护使升级链在「全新库」与「历史库」下都能幂等通过。
    """
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def _add_column(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column(
        "nutrition_goals",
        sa.Column("calories_min", sa.Float(), server_default=sa.text("1860"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("calories_max", sa.Float(), server_default=sa.text("2140"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("protein_min", sa.Float(), server_default=sa.text("65"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("protein_max", sa.Float(), server_default=sa.text("85"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("carb_min", sa.Float(), server_default=sa.text("200"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("carb_max", sa.Float(), server_default=sa.text("260"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("fat_min", sa.Float(), server_default=sa.text("45"), nullable=False),
    )
    _add_column(
        "nutrition_goals",
        sa.Column("fat_max", sa.Float(), server_default=sa.text("65"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("nutrition_goals", "fat_max")
    op.drop_column("nutrition_goals", "fat_min")
    op.drop_column("nutrition_goals", "carb_max")
    op.drop_column("nutrition_goals", "carb_min")
    op.drop_column("nutrition_goals", "protein_max")
    op.drop_column("nutrition_goals", "protein_min")
    op.drop_column("nutrition_goals", "calories_max")
    op.drop_column("nutrition_goals", "calories_min")
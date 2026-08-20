"""add lifestyle constraint fields to user_profiles

Revision ID: 0004_add_lifestyle_constraints
Revises: 0003_add_nutrition_goal_range
Create Date: 2026-08-13

阶段1（用户档案采集）：为 ``UserProfile`` 新增生活约束结构化字段——
``cooking_skill``（烹饪能力枚举 beginner/intermediate/proficient）、
``kitchenware``（可用厨具列表，JSON）、``prep_time_max``（最长备餐时间，分钟）。

``cooking_skill`` / ``prep_time_max`` 设服务端默认值，兼容历史数据；
``kitchenware`` 因 JSON 列在 MySQL/PostgreSQL 上不支持字面量默认值，
采用"先加可空列 → 回填空列表 → 收紧 NOT NULL"的三步式，与模型语义保持一致。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0004_add_lifestyle_constraints"
down_revision: str = "0003_add_nutrition_goal_range"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    """判断列是否已存在（跨方言）。全新库经 0001 create_all 时这些列已在，需幂等跳过。"""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("user_profiles", "cooking_skill"):
        op.add_column(
            "user_profiles",
            sa.Column(
                "cooking_skill",
                sa.String(20),
                server_default=sa.text("'intermediate'"),
                nullable=False,
            ),
        )
    if not _has_column("user_profiles", "prep_time_max"):
        op.add_column(
            "user_profiles",
            sa.Column("prep_time_max", sa.Integer(), server_default=sa.text("60"), nullable=False),
        )
    if not _has_column("user_profiles", "kitchenware"):
        op.add_column("user_profiles", sa.Column("kitchenware", sa.JSON(), nullable=True))
        op.execute("UPDATE user_profiles SET kitchenware = '[]' WHERE kitchenware IS NULL")
        # MySQL 的 MODIFY COLUMN 需要显式提供现有类型，否则 Alembic 无法生成变更语句。
        op.alter_column(
            "user_profiles", "kitchenware", existing_type=sa.JSON(), nullable=False
        )


def downgrade() -> None:
    op.drop_column("user_profiles", "kitchenware")
    op.drop_column("user_profiles", "prep_time_max")
    op.drop_column("user_profiles", "cooking_skill")

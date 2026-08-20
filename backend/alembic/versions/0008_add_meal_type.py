"""add meal type to plan meals

Revision ID: 0008_add_meal_type
Revises: 0007_add_meal_checkin_fields
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0008_add_meal_type"
down_revision: str = "0007_add_meal_checkin_fields"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("plan_meal_items", "meal_type"):
        op.add_column(
            "plan_meal_items",
            sa.Column("meal_type", sa.String(length=10), nullable=False, server_default="晚餐"),
        )


def downgrade() -> None:
    op.drop_column("plan_meal_items", "meal_type")

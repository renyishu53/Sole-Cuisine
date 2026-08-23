"""Add explicit procurement origin for shopping items.

Revision ID: 0012_add_shopping_origin
Revises: 0011_normalize_shopping_categories
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_add_shopping_origin"
down_revision = "0011_normalize_shopping_cat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan_shopping_items",
        sa.Column("origin", sa.String(length=24), nullable=False, server_default="meal_ingredient"),
    )
    op.create_index("ix_plan_shopping_items_origin", "plan_shopping_items", ["origin"])


def downgrade() -> None:
    op.drop_index("ix_plan_shopping_items_origin", table_name="plan_shopping_items")
    op.drop_column("plan_shopping_items", "origin")

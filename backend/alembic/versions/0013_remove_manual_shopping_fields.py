"""Remove fields used by manual shopping-list mutations.

Revision ID: 0013_remove_manual_shopping_fields
Revises: 0012_add_shopping_origin
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_remove_manual_shopping_fields"
down_revision = "0012_add_shopping_origin"
branch_labels = None
depends_on = None


def _table_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("plan_shopping_items")}


def _table_indexes() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes("plan_shopping_items")}


def upgrade() -> None:
    """Keep shopping rows solely as plan-derived procurement records."""
    columns = _table_columns()
    indexes = _table_indexes()
    with op.batch_alter_table("plan_shopping_items") as batch:
        if "ix_plan_shopping_items_origin" in indexes:
            batch.drop_index("ix_plan_shopping_items_origin")
        for column in ("origin", "substituted_from", "substituted_accepted"):
            if column in columns:
                batch.drop_column(column)


def downgrade() -> None:
    """Restore removed columns for a downgrade to the legacy shopping model."""
    columns = _table_columns()
    with op.batch_alter_table("plan_shopping_items") as batch:
        if "origin" not in columns:
            batch.add_column(
                sa.Column(
                    "origin",
                    sa.String(length=24),
                    nullable=False,
                    server_default="meal_ingredient",
                )
            )
        if "substituted_from" not in columns:
            batch.add_column(sa.Column("substituted_from", sa.String(length=120), nullable=True))
        if "substituted_accepted" not in columns:
            batch.add_column(sa.Column("substituted_accepted", sa.Boolean(), nullable=True))
        batch.create_index("ix_plan_shopping_items_origin", ["origin"])

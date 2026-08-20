"""add user avatar url

Revision ID: 0009_add_user_avatar_url
Revises: 0008_add_meal_type
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0009_add_user_avatar_url"
down_revision: str = "0008_add_meal_type"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("users", "avatar_url"):
        op.add_column(
            "users",
            sa.Column("avatar_url", sa.String(length=500), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("users", "avatar_url")

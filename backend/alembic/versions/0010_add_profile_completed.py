"""add explicit user profile completion flag

Revision ID: 0010_add_profile_completed
Revises: 0009_add_user_avatar_url
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0010_add_profile_completed"
down_revision: str = "0009_add_user_avatar_url"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("user_profiles", "profile_completed"):
        op.add_column(
            "user_profiles",
            sa.Column(
                "profile_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        # 兼容已有用户：过去只有生成营养目标后才会被前端视为已建档。
        op.execute(
            sa.text(
                "UPDATE user_profiles SET profile_completed = TRUE "
                "WHERE user_id IN (SELECT user_id FROM nutrition_goals) "
                "OR height_cm <> 170 OR weight_kg <> 65 OR age <> 30 "
                "OR gender <> 'male' OR activity_level <> 'moderate' "
                "OR budget_limit <> 500"
            )
        )


def downgrade() -> None:
    if _has_column("user_profiles", "profile_completed"):
        op.drop_column("user_profiles", "profile_completed")

"""normalize shopping item categories to the five product groups"""

from alembic import op
import sqlalchemy as sa


revision = "0011_normalize_shopping_cat"
down_revision = "0010_add_profile_completed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE plan_shopping_items SET category = CASE "
            "WHEN category IN ('肉类', '蛋类', '乳制品', '奶制品') THEN '肉蛋奶' "
            "WHEN category IN ('调味料', '调味品', '日用品', '未分类', '杂项') THEN '其他' "
            "WHEN category NOT IN ('肉蛋奶', '蔬菜', '主食', '水果', '其他') THEN '其他' "
            "ELSE category END"
        )
    )


def downgrade() -> None:
    pass

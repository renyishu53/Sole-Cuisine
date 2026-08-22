"""add plan conflict detail fields

Revision ID: 0006_add_plan_conflict_details
Revises: 0005_add_shopping_substitution_fields
Create Date: 2026-08-14

阶段3（任务A 校验失败三级策略）：为 ``WeeklyPlan`` 新增四列，把结构化校验结果
落库，供计划详情页展示换菜选项（第 2 级降级提示）与自动修正/人工接管提示：

- ``conflict_details``（JSON，硬/软冲突明细 + 降级选项，默认 []）
- ``auto_fixes``（JSON，第 1 级自动修正说明，默认 []）
- ``needs_manual_review``（Boolean，第 3 级人工接管标记，默认 false）
- ``manual_review_hint``（String(500)，人工接管提示，默认 ""）

历史计划（手动维护/早期生成）四列均为默认值，向后兼容。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0006_add_plan_conflict_details"
down_revision: str = "0005_shopping_sub_fields"
branch_labels: None | str = None
depends_on: None | str = None


def _has_column(table: str, column: str) -> bool:
    """判断列是否已存在（跨方言）。全新库经 0001 create_all 时这些列已在，需幂等跳过。"""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    # MySQL 上 JSON 列不支持字面量默认值，且 MODIFY COLUMN 需显式提供现有类型；
    # 故 JSON 列采用「先加可空列 → 回填空列表 → 收紧 NOT NULL」三步式（同 0004）。
    if not _has_column("weekly_plans", "conflict_details"):
        op.add_column("weekly_plans", sa.Column("conflict_details", sa.JSON(), nullable=True))
        op.execute("UPDATE weekly_plans SET conflict_details = '[]' WHERE conflict_details IS NULL")
        op.alter_column(
            "weekly_plans", "conflict_details", existing_type=sa.JSON(), nullable=False
        )
    if not _has_column("weekly_plans", "auto_fixes"):
        op.add_column("weekly_plans", sa.Column("auto_fixes", sa.JSON(), nullable=True))
        op.execute("UPDATE weekly_plans SET auto_fixes = '[]' WHERE auto_fixes IS NULL")
        op.alter_column(
            "weekly_plans", "auto_fixes", existing_type=sa.JSON(), nullable=False
        )
    if not _has_column("weekly_plans", "needs_manual_review"):
        op.add_column(
            "weekly_plans",
            sa.Column("needs_manual_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column("weekly_plans", "manual_review_hint"):
        op.add_column(
            "weekly_plans",
            sa.Column("manual_review_hint", sa.String(500), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("weekly_plans", "manual_review_hint")
    op.drop_column("weekly_plans", "needs_manual_review")
    op.drop_column("weekly_plans", "auto_fixes")
    op.drop_column("weekly_plans", "conflict_details")

"""initial solo-chef schema

Revision ID: 0001_initial_solochef
Revises:
Create Date: 2026-08-07

Dialect-agnostic bootstrap: create all tables from the current SQLAlchemy
metadata. Works on SQLite (dev), MySQL (production target) and PostgreSQL.
Previous CasaMind family-oriented migrations were removed during the
de-family-ification that introduced the SoloChef single-user model.
"""

from collections.abc import Sequence

from alembic import op
from app.db.base import Base
import app.models  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "0001_initial_solochef"
down_revision: None | str = None
branch_labels: None | str = None
depends_on: None | str = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)

"""Baseline revision (no-op).

Runtime DDL remains owned by ``infrastructure.database.DatabaseManager``.
Use Alembic for additive migrations (new columns, indexes) on shared DBs.

Revision ID: 001_baseline_noop
Revises:
Create Date: 2026-04-29
"""

from typing import Sequence, Union

revision: str = "001_baseline_noop"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

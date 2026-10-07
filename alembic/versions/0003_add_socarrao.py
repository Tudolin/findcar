"""add SóCarrão as a source to existing saved searches

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE saved_search SET sources = sources || '[\"socarrao\"]'::jsonb "
        "WHERE NOT sources @> '[\"socarrao\"]'::jsonb"
    )
    op.execute(
        "INSERT INTO source_status (name, enabled, last_count) VALUES ('socarrao', true, 0) "
        "ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("UPDATE saved_search SET sources = sources - 'socarrao'")

"""enable OLX (now read through headless Chromium)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only flips the row the v0.1 seed created; a source the user disabled stays disabled.
    op.execute(
        "UPDATE source_status SET enabled = true, last_status = NULL, last_error = NULL "
        "WHERE name = 'olx' AND last_error LIKE '403 Cloudflare na investiga%'"
    )


def downgrade() -> None:
    pass

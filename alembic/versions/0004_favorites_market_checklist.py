"""favorites tracking, market fields, visit checklist, seller phone

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jsonb = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.add_column("vehicle", sa.Column("favorited_at", sa.DateTime(), nullable=True))
    op.add_column("vehicle", sa.Column("favorite_price", sa.Integer(), nullable=True))
    op.add_column("vehicle", sa.Column("checklist", jsonb, nullable=False, server_default="{}"))
    op.add_column("vehicle", sa.Column("listed_since", sa.DateTime(), nullable=True))
    op.add_column("vehicle", sa.Column("price_drop", sa.Integer(), nullable=True))
    op.add_column("vehicle", sa.Column("price_drop_at", sa.DateTime(), nullable=True))
    op.add_column("listing", sa.Column("seller_phone", sa.String(), nullable=True))
    # Existing favorites: assume they were favorited at today's price.
    op.execute("UPDATE vehicle SET favorited_at = updated_at, favorite_price = price WHERE favorite")
    op.execute(
        "UPDATE vehicle v SET listed_since = s.since FROM ("
        " SELECT vehicle_id, MIN(LEAST(first_seen, COALESCE(published_at, first_seen))) AS since"
        " FROM listing GROUP BY vehicle_id) s WHERE s.vehicle_id = v.id"
    )


def downgrade() -> None:
    for col in ("favorited_at", "favorite_price", "checklist", "listed_since", "price_drop", "price_drop_at"):
        op.drop_column("vehicle", col)
    op.drop_column("listing", "seller_phone")

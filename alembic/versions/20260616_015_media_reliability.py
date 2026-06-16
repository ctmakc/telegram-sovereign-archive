"""Media reliability: download_status lifecycle, attempts, error, skip reason, pHash.

Epic B. Adds richer media lifecycle on top of the boolean `downloaded`:
- download_status: pending / downloaded / failed / skipped / unavailable
- download_attempts, last_download_error — retry-queue + diagnostics
- skipped_reason — why an item was skipped (e.g. over MAX_MEDIA_SIZE_MB), so it
  stays visible as an incomplete archive item rather than silently missing
- perceptual_hash — near-duplicate image detection

Existing rows are backfilled: downloaded=1 -> 'downloaded', else 'pending'.

Revision ID: 015
Revises: 014
Create Date: 2026-06-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c["name"] for c in inspector.get_columns("media")}

    if "perceptual_hash" not in existing_cols:
        op.add_column("media", sa.Column("perceptual_hash", sa.String(64), nullable=True))
    if "download_status" not in existing_cols:
        op.add_column(
            "media", sa.Column("download_status", sa.String(20), nullable=False, server_default="pending")
        )
    if "download_attempts" not in existing_cols:
        op.add_column("media", sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"))
    if "last_download_error" not in existing_cols:
        op.add_column("media", sa.Column("last_download_error", sa.Text(), nullable=True))
    if "skipped_reason" not in existing_cols:
        op.add_column("media", sa.Column("skipped_reason", sa.String(255), nullable=True))

    # Backfill status from the legacy boolean so existing archives are accurate.
    op.execute("UPDATE media SET download_status = 'downloaded' WHERE downloaded = 1")
    op.execute("UPDATE media SET download_status = 'pending' WHERE downloaded = 0 OR downloaded IS NULL")

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("media")}
    if "idx_media_download_status" not in existing_indexes:
        op.create_index("idx_media_download_status", "media", ["download_status"])


def downgrade() -> None:
    op.drop_index("idx_media_download_status", table_name="media")
    op.drop_column("media", "skipped_reason")
    op.drop_column("media", "last_download_error")
    op.drop_column("media", "download_attempts")
    op.drop_column("media", "download_status")
    op.drop_column("media", "perceptual_hash")

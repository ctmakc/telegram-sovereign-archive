"""Sovereign archive layer: deletion tombstones, message versions, event log.

Adds append-only / evidence-grade structures so SAFE_ARCHIVE_MODE can preserve
local truth:

- messages.is_deleted_in_telegram / deleted_detected_at — a Telegram deletion
  flags the row instead of removing it (tombstone).
- messages.first_seen_at / last_seen_at — provenance timestamps.
- message_versions — every prior text is snapshotted before an edit overwrites
  the live row (no version is ever lost).
- message_events — append-only log of created/edited/deleted/... mutations.

message_versions and message_events intentionally have NO foreign key to
messages: they are evidence and must outlive even a hard delete of the live row.

Revision ID: 014
Revises: 013
Create Date: 2026-06-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- messages: tombstone + provenance columns -------------------------
    existing_cols = {c["name"] for c in inspector.get_columns("messages")}
    if "is_deleted_in_telegram" not in existing_cols:
        op.add_column(
            "messages",
            sa.Column("is_deleted_in_telegram", sa.Integer(), nullable=False, server_default="0"),
        )
    if "deleted_detected_at" not in existing_cols:
        op.add_column("messages", sa.Column("deleted_detected_at", sa.DateTime(), nullable=True))
    if "first_seen_at" not in existing_cols:
        op.add_column(
            "messages", sa.Column("first_seen_at", sa.DateTime(), nullable=True, server_default=sa.func.now())
        )
    if "last_seen_at" not in existing_cols:
        op.add_column(
            "messages", sa.Column("last_seen_at", sa.DateTime(), nullable=True, server_default=sa.func.now())
        )

    existing_msg_indexes = {idx["name"] for idx in inspector.get_indexes("messages")}
    if "idx_messages_deleted" not in existing_msg_indexes:
        op.create_index("idx_messages_deleted", "messages", ["chat_id", "is_deleted_in_telegram"])

    # --- message_versions -------------------------------------------------
    existing_tables = set(inspector.get_table_names())
    if "message_versions" not in existing_tables:
        op.create_table(
            "message_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("edit_date", sa.DateTime(), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("raw_json", sa.Text(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
            sa.UniqueConstraint("message_id", "chat_id", "version_number", name="uq_message_version"),
        )
        op.create_index("idx_message_versions_msg", "message_versions", ["chat_id", "message_id"])

    # --- message_events ---------------------------------------------------
    if "message_events" not in existing_tables:
        op.create_table(
            "message_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("event_date", sa.DateTime(), nullable=True),
            sa.Column("raw_json", sa.Text(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        )
        op.create_index("idx_message_events_msg", "message_events", ["chat_id", "message_id"])
        op.create_index("idx_message_events_type", "message_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("idx_message_events_type", table_name="message_events")
    op.drop_index("idx_message_events_msg", table_name="message_events")
    op.drop_table("message_events")

    op.drop_index("idx_message_versions_msg", table_name="message_versions")
    op.drop_table("message_versions")

    op.drop_index("idx_messages_deleted", table_name="messages")
    op.drop_column("messages", "last_seen_at")
    op.drop_column("messages", "first_seen_at")
    op.drop_column("messages", "deleted_detected_at")
    op.drop_column("messages", "is_deleted_in_telegram")

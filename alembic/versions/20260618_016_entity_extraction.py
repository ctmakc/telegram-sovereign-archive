"""Entity extraction: entities + entity_mentions.

Deterministic regex extraction (emails/urls/phones/crypto wallets/amounts) stored
as deduplicated Entity rows with per-occurrence EntityMention rows, so deals can be
found by entity (PRD §9 / §12.10 / §16 deal mode). No LLM involved.

Revision ID: 016
Revises: 015
Create Date: 2026-06-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "entities" not in tables:
        op.create_table(
            "entities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entity_type", sa.String(32), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("normalized_value", sa.String(512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
            sa.UniqueConstraint("entity_type", "normalized_value", name="uq_entity"),
        )
        op.create_index("idx_entities_type", "entities", ["entity_type"])

    if "entity_mentions" not in tables:
        op.create_table(
            "entity_mentions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("offset_start", sa.Integer(), nullable=True),
            sa.Column("offset_end", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        )
        op.create_index("idx_entity_mentions_entity", "entity_mentions", ["entity_id"])
        op.create_index("idx_entity_mentions_msg", "entity_mentions", ["chat_id", "message_id"])


def downgrade() -> None:
    op.drop_index("idx_entity_mentions_msg", table_name="entity_mentions")
    op.drop_index("idx_entity_mentions_entity", table_name="entity_mentions")
    op.drop_table("entity_mentions")
    op.drop_index("idx_entities_type", table_name="entities")
    op.drop_table("entities")

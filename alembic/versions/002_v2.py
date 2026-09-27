"""v2 update: media keys, pins, seals, labels, quiet hours, group invites

Revision ID: 002_v2
Revises: 001_initial
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_v2"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pk():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    # users: real names, quiet hours, sealed-by-default toggle
    op.add_column("users", sa.Column("first_name", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("seal_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("quiet_from", sa.String(5), nullable=True))
    op.add_column("users", sa.Column("quiet_to", sa.String(5), nullable=True))

    # whispers: media key reference, envelope label, sealing, replies
    op.add_column("whispers", sa.Column("media_key", sa.String(16), nullable=True))
    op.add_column("whispers", sa.Column("label", sa.String(8), nullable=True))
    op.add_column("whispers", sa.Column("is_sealed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("whispers", sa.Column("seal_key_enc", sa.Text(), nullable=True))
    op.add_column("whispers", sa.Column("reply_to_id", sa.BigInteger(), nullable=True))

    # groups: auto-join invite links
    op.add_column("groups", sa.Column("invite_token", sa.String(64), nullable=True))
    op.add_column("groups", sa.Column("auto_join", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("uq_groups_invite_token", "groups", ["invite_token"], unique=True)

    # contacts: names and favourites
    op.add_column("contacts", sa.Column("first_name", sa.String(64), nullable=True))
    op.add_column("contacts", sa.Column("last_name", sa.String(64), nullable=True))
    op.add_column("contacts", sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "media_keys",
        sa.Column("id", _pk(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("media_group", sa.String(64), nullable=True),
        sa.Column("files", sa.Text(), nullable=False),
        sa.Column("multi_use", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_media_keys_key"),
    )
    op.create_index("ix_media_keys_owner_id", "media_keys", ["owner_id"])
    op.create_index("ix_media_keys_media_group", "media_keys", ["media_group"])

    op.create_table(
        "pins",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("whisper_id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pins")
    op.drop_index("ix_media_keys_media_group", table_name="media_keys")
    op.drop_index("ix_media_keys_owner_id", table_name="media_keys")
    op.drop_table("media_keys")
    op.drop_column("contacts", "favorite")
    op.drop_column("contacts", "last_name")
    op.drop_column("contacts", "first_name")
    op.drop_index("uq_groups_invite_token", table_name="groups")
    op.drop_column("groups", "auto_join")
    op.drop_column("groups", "invite_token")
    op.drop_column("whispers", "reply_to_id")
    op.drop_column("whispers", "seal_key_enc")
    op.drop_column("whispers", "is_sealed")
    op.drop_column("whispers", "label")
    op.drop_column("whispers", "media_key")
    op.drop_column("users", "quiet_to")
    op.drop_column("users", "quiet_from")
    op.drop_column("users", "seal_default")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")

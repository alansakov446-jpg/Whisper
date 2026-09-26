"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-04-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pk():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("lang", sa.String(8), nullable=False, server_default="ru"),
        sa.Column("pseudonym", sa.String(64), nullable=True),
        sa.Column("default_ttl", sa.Integer(), nullable=True),
        sa.Column("default_mode", sa.String(16), nullable=False, server_default="open"),
        sa.Column("notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "whispers",
        sa.Column("id", _pk(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_id_enc", sa.Text(), nullable=True),
        sa.Column("sender_hmac", sa.String(64), nullable=True),
        sa.Column("recipient_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_username", sa.String(64), nullable=True),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("group_name", sa.String(128), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("media_type", sa.String(32), nullable=True),
        sa.Column("media_file_id", sa.Text(), nullable=True),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("ttl_explicit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_mode", sa.String(16), nullable=False, server_default="inline"),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("lang", sa.String(8), nullable=False, server_default="ru"),
        sa.Column("inline_message_id", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("delivery_refs", sa.Text(), nullable=True),
        sa.Column("pending_targets", sa.Text(), nullable=True),
        sa.Column("pseudonym_snapshot", sa.String(64), nullable=True),
        sa.Column("sender_username", sa.String(64), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token", name="uq_whispers_token"),
    )
    op.create_index("ix_whispers_sender_id", "whispers", ["sender_id"])
    op.create_index("ix_whispers_recipient_username", "whispers", ["recipient_username"])
    op.create_index("ix_whispers_expires", "whispers", ["expires_at"])
    op.create_index("ix_whispers_send_at", "whispers", ["send_at"])
    op.create_index("ix_whispers_sender_hmac", "whispers", ["sender_hmac"])
    op.create_index("ix_whispers_recipient", "whispers", ["recipient_id"])
    op.create_index("ix_whispers_status", "whispers", ["status"])
    op.create_index("ix_whispers_group", "whispers", ["group_id"])

    op.create_table(
        "groups",
        sa.Column("id", _pk(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("creator_id", sa.BigInteger(), nullable=False),
        sa.Column("successor_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_groups_creator_id", "groups", ["creator_id"])

    op.create_table(
        "group_members",
        sa.Column("group_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    op.create_table(
        "group_invites",
        sa.Column("id", _pk(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("invited_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "username", name="uq_invite_group_username"),
    )
    op.create_index("ix_group_invites_group_id", "group_invites", ["group_id"])
    op.create_index("ix_group_invites_username", "group_invites", ["username"])

    op.create_table(
        "read_receipts",
        sa.Column("whisper_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "contacts",
        sa.Column("id", _pk(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contacts_owner", "contacts", ["owner_id"])

    op.create_table(
        "pending_media",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pending_media")
    op.drop_table("contacts")
    op.drop_table("read_receipts")
    op.drop_table("group_invites")
    op.drop_table("group_members")
    op.drop_table("groups")
    op.drop_table("whispers")
    op.drop_table("users")

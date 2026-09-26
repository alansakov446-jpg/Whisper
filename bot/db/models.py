from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base
from bot.utils.time import utcnow


def _pk():
    # BIGSERIAL on Postgres, INTEGER PRIMARY KEY on SQLite (autoincrement).
    return BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    pseudonym: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Whisper(Base):
    __tablename__ = "whispers"
    __table_args__ = (
        Index("ix_whispers_expires", "expires_at"),
        Index("ix_whispers_send_at", "send_at"),
        Index("ix_whispers_sender_hmac", "sender_hmac"),
        Index("ix_whispers_recipient", "recipient_id"),
        Index("ix_whispers_status", "status"),
        Index("ix_whispers_group", "group_id"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    sender_id_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recipient_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttl_explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="inline")
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    inline_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_targets: Mapped[str | None] = mapped_column(Text, nullable=True)
    pseudonym_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    successor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class GroupInvite(Base):
    __tablename__ = "group_invites"
    __table_args__ = (UniqueConstraint("group_id", "username", name="uq_invite_group_username"),)

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    invited_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ReadReceipt(Base):
    __tablename__ = "read_receipts"

    whisper_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_owner", "owner_id"),)

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contact_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PendingMedia(Base):
    __tablename__ = "pending_media"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

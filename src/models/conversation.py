"""Conversation container: metadata and summary boundary only."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin
from models.types import JsonDict


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), default="New Chat", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_until_message_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    summary_until_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    meta: Mapped[dict] = mapped_column(
        "metadata", JsonDict, default=dict, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_conversations_user_session"),
        CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_conversations_status",
        ),
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index(
            "ix_conversations_user_status_updated", "user_id", "status", "updated_at"
        ),
    )

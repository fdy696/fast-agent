"""Chat session metadata and rolling-summary checkpoint."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), default="New Chat", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    summary_message_list: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    summarized_through_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

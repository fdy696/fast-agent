"""Token and cost ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(64), default="deepseek-v4-pro", nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_token_usage_run_id"),
        CheckConstraint("prompt_tokens >= 0", name="ck_tu_prompt_tokens_non_negative"),
        CheckConstraint("cached_tokens >= 0", name="ck_tu_cached_tokens_non_negative"),
        CheckConstraint("completion_tokens >= 0", name="ck_tu_completion_tokens_non_negative"),
        CheckConstraint("total_tokens >= 0", name="ck_tu_total_tokens_non_negative"),
        CheckConstraint("cost >= 0", name="ck_tu_cost_non_negative"),
        Index("ix_tu_user_created", "user_id", "created_at"),
        Index("ix_tu_conv_created", "conversation_id", "created_at"),
        Index("ix_tu_model_created", "model", "created_at"),
    )

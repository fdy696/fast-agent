"""Logical agent execution steps. Never write one row per token/chunk."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from models.types import JsonDict


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    input: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    output: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_step"),
        CheckConstraint(
            "category IN ('llm_call', 'tool_call', 'retrieval', 'reasoning', 'routing')",
            name="ck_agent_steps_category",
        ),
        CheckConstraint("type IN ('model', 'tool', 'skill', 'memory')", name="ck_agent_steps_type"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed', 'cancelled')", name="ck_agent_steps_status"),
        Index("ix_as_category_created", "category", "created_at"),
        Index("ix_as_type_name", "type", "name"),
    )

"""Store one complete PydanticAI run per chat message row.

Revision ID: a002_store_one_agent_run_per_row
Revises: a001_rebuild_chat_schema
Create Date: 2026-07-11

This migration intentionally deletes the previous chat history schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a002_store_one_agent_run_per_row"
down_revision: str | None = "a001_rebuild_chat_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_chat_sessions_summary_msg", "chat_sessions", type_="foreignkey"
    )
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), server_default="New Chat", nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("summary_message_list", postgresql.JSONB(), nullable=True),
        sa.Column(
            "summarized_through_message_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        sa.CheckConstraint(
            "status IN ('active','archived','deleted')",
            name="ck_chat_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"])
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("message_list", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "run_id", name="uq_chat_message_run"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.session_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_chat_message_session_id", "chat_messages", ["session_id", "id"]
    )


def downgrade() -> None:
    raise RuntimeError("The previous chat history schema is intentionally unsupported")

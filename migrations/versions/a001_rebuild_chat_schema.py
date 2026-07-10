"""Rebuild chat schema — chat_sessions + chat_messages replace 6 tables.

Revision ID: a001_rebuild_chat_schema
Revises: dd788e00562f
Create Date: 2026-07-10
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a001_rebuild_chat_schema"
down_revision: Union[str, None] = "dd788e00562f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Drop old tables
    op.drop_table("artifacts")
    op.drop_table("token_usage")
    op.drop_table("agent_steps")
    op.drop_constraint("fk_cm_run_id", "conversation_messages", type_="foreignkey")
    op.drop_constraint("fk_conversations_summary_until_message_id", "conversations", type_="foreignkey")
    op.drop_table("agent_runs")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")

    # chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="New Chat"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("history_summary", sa.Text(), nullable=True),
        sa.Column("summarized_through_message_id", sa.BigInteger(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        sa.CheckConstraint("status IN ('active','archived','deleted')", name="ck_chat_sessions_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_chat_sessions_user_id", ondelete="RESTRICT"),
    )
    op.create_index("ix_chat_sessions_id", "chat_sessions", ["id"])
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"])

    # chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("message_data", JSON_DICT, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_chat_message_status",
        ),
        sa.UniqueConstraint("turn_id", "message_index", name="uq_chat_message_turn_index"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.session_id"],
            name="fk_chat_messages_session_id", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_chat_messages_id", "chat_messages", ["id"])
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_turn_id", "chat_messages", ["turn_id"])
    op.create_index("idx_cm_session_status_id", "chat_messages", ["session_id", "status", "id"])

    op.create_foreign_key(
        "fk_chat_sessions_summary_msg",
        "chat_sessions", "chat_messages",
        ["summarized_through_message_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_sessions_summary_msg", "chat_sessions", type_="foreignkey")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")

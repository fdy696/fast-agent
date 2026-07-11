"""create agent schema - 6 tables

Revision ID: dd788e00562f
Revises: 9a7d3f1c5e8b
Create Date: 2026-06-28 19:13:47.780697
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dd788e00562f"
down_revision: str | None = "9a7d3f1c5e8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="New Chat"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_until_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "summary_until_created_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("metadata", JSON_DICT, nullable=False, server_default="{}"),
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
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_conversations_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_conversations_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id", "session_id", name="uq_conversations_user_session"
        ),
    )
    op.create_index(op.f("ix_conversations_id"), "conversations", ["id"])
    op.create_index(
        op.f("ix_conversations_session_id"), "conversations", ["session_id"]
    )
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"])
    op.create_index(
        "ix_conversations_user_updated", "conversations", ["user_id", "updated_at"]
    )
    op.create_index(
        "ix_conversations_user_status_updated",
        "conversations",
        ["user_id", "status", "updated_at"],
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_data", JSON_DICT, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", JSON_DICT, nullable=False, server_default="{}"),
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
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_cm_role"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_cm_status",
        ),
        sa.UniqueConstraint(
            "turn_id", "message_index", name="uq_cm_turn_message_index"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_cm_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_cm_user_id", ondelete="RESTRICT"
        ),
    )
    op.create_index(
        op.f("ix_conversation_messages_id"), "conversation_messages", ["id"]
    )
    op.create_index(
        op.f("ix_conversation_messages_conversation_id"),
        "conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        op.f("ix_conversation_messages_user_id"), "conversation_messages", ["user_id"]
    )
    op.create_index(
        op.f("ix_conversation_messages_turn_id"), "conversation_messages", ["turn_id"]
    )
    op.create_index("ix_cm_conv_id", "conversation_messages", ["conversation_id", "id"])
    op.create_index(
        "ix_cm_user_created", "conversation_messages", ["user_id", "created_at"]
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_message_id", sa.Integer(), nullable=True),
        sa.Column("assistant_message_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_ar_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_ar_user_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["conversation_messages.id"],
            name="fk_ar_user_msg",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["conversation_messages.id"],
            name="fk_ar_assistant_msg",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("run_id", name="uq_agent_runs_run_id"),
    )
    op.create_index(op.f("ix_agent_runs_run_id"), "agent_runs", ["run_id"])
    op.create_index(
        op.f("ix_agent_runs_conversation_id"), "agent_runs", ["conversation_id"]
    )
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"])
    op.create_index(
        "ix_ar_conv_created", "agent_runs", ["conversation_id", "created_at"]
    )
    op.create_index("ix_ar_user_created", "agent_runs", ["user_id", "created_at"])
    op.create_index("ix_ar_status_created", "agent_runs", ["status", "created_at"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("input", JSON_DICT, nullable=True),
        sa.Column("output", JSON_DICT, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "category IN ('llm_call', 'tool_call', 'retrieval', 'reasoning', 'routing')",
            name="ck_agent_steps_category",
        ),
        sa.CheckConstraint(
            "type IN ('model', 'tool', 'skill', 'memory')", name="ck_agent_steps_type"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_steps_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_as_run_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_step"),
    )
    op.create_index(op.f("ix_agent_steps_run_id"), "agent_steps", ["run_id"])
    op.create_index("ix_as_category_created", "agent_steps", ["category", "created_at"])
    op.create_index("ix_as_type_name", "agent_steps", ["type", "name"])

    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column(
            "model", sa.String(64), nullable=False, server_default="deepseek-v4-pro"
        ),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "prompt_tokens >= 0", name="ck_tu_prompt_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "cached_tokens >= 0", name="ck_tu_cached_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "completion_tokens >= 0", name="ck_tu_completion_tokens_non_negative"
        ),
        sa.CheckConstraint("total_tokens >= 0", name="ck_tu_total_tokens_non_negative"),
        sa.CheckConstraint("cost >= 0", name="ck_tu_cost_non_negative"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_tu_user_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_tu_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_tu_run_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_id", name="uq_token_usage_run_id"),
    )
    op.create_index(op.f("ix_token_usage_user_id"), "token_usage", ["user_id"])
    op.create_index(
        op.f("ix_token_usage_conversation_id"), "token_usage", ["conversation_id"]
    )
    op.create_index(op.f("ix_token_usage_run_id"), "token_usage", ["run_id"])
    op.create_index("ix_tu_user_created", "token_usage", ["user_id", "created_at"])
    op.create_index(
        "ix_tu_conv_created", "token_usage", ["conversation_id", "created_at"]
    )
    op.create_index("ix_tu_model_created", "token_usage", ["model", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("source_step_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("data", JSON_DICT, nullable=False, server_default="{}"),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("file_id", sa.String(256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'deleted')",
            name="ck_artifacts_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_artifacts_user_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_artifacts_conv_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.run_id"],
            name="fk_artifacts_run_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["conversation_messages.id"],
            name="fk_artifacts_msg_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_step_id"],
            ["agent_steps.id"],
            name="fk_artifacts_step_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("artifact_id", name="uq_artifacts_artifact_id"),
    )
    op.create_index(op.f("ix_artifacts_artifact_id"), "artifacts", ["artifact_id"])
    op.create_index(op.f("ix_artifacts_user_id"), "artifacts", ["user_id"])
    op.create_index(
        op.f("ix_artifacts_conversation_id"), "artifacts", ["conversation_id"]
    )
    op.create_index("ix_artifacts_user_created", "artifacts", ["user_id", "created_at"])
    op.create_index(
        "ix_artifacts_conv_created", "artifacts", ["conversation_id", "created_at"]
    )
    op.create_index("ix_artifacts_type_status", "artifacts", ["type", "status"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("token_usage")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")

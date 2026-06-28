"""add user_id session_id unique constraint

Revision ID: 20260628_session_ownership
Revises: 9a7d3f1c5e8b
Create Date: 2026-06-28
"""
from alembic import op

revision = "20260628_session_ownership"
down_revision = "9a7d3f1c5e8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_conversations_user_session",
        "conversations",
        ["user_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_conversations_user_session",
        "conversations",
        type_="unique",
    )

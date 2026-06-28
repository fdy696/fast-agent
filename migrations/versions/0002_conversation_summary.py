"""conversation summary cursor

Revision ID: 0002_conv_summary
Revises: 04cbfe3b3dd1
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_conv_summary"
down_revision: Union[str, None] = "04cbfe3b3dd1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_cursor", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_cursor")
    op.drop_column("conversations", "summary")

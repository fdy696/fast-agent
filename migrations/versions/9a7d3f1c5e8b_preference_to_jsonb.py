"""preference_to_jsonb

Revision ID: 9a7d3f1c5e8b
Revises: 04cbfe3b3dd1
Create Date: 2026-06-27 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9a7d3f1c5e8b'
down_revision: Union[str, None] = '0002_conv_summary'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(r"""
        ALTER TABLE users
        ALTER COLUMN preference TYPE jsonb
        USING CASE
            WHEN preference IS NULL THEN NULL
            WHEN preference ~ '^\s*\{' THEN preference::jsonb
            ELSE '{}'::jsonb
        END
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users
        ALTER COLUMN preference TYPE varchar(500)
        USING preference::text
    """)

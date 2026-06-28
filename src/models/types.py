"""Shared SQLAlchemy column types."""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

JsonDict = JSON().with_variant(JSONB, "postgresql")

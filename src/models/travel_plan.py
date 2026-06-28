from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TravelPlan(Base):
    __tablename__ = "travel_plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    plan_data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)

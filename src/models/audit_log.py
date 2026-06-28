from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, ToDictMixin


class AuditLog(Base, TimestampMixin, ToDictMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # 故意不加 ForeignKey：审计日志独立于用户生命周期，用户删除后日志仍可追溯
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    response_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_args: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

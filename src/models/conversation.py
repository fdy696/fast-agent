from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="新对话")
    history_messages: Mapped[list] = mapped_column(JSON, default=list)
    # 滚动摘要：覆盖 history_messages[0:summary_cursor] 的浓缩文本；None 表示尚无摘要
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 压缩游标：history_messages[:summary_cursor] 已被摘要吃掉，[cursor:] 仍原文保留
    summary_cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_conversations_user_session"),
    )

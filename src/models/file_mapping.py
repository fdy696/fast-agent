from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, ToDictMixin


class FileMapping(Base, TimestampMixin, ToDictMixin):
    __tablename__ = "file_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    file_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 故意不加 ForeignKey：保留历史映射记录，独立于用户生命周期
    upload_user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

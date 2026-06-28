from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import FileMapping


class FileMappingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_file_mapping(
        self,
        *,
        file_id: str,
        original_name: str,
        file_type: str,
        file_size: int | None,
        user_id: int,
        file_path: str | None = None,
    ) -> FileMapping:
        mapping = FileMapping(
            file_id=file_id,
            original_filename=original_name,
            file_type=file_type,
            file_size=file_size,
            upload_user_id=user_id,
            file_path=file_path,
        )
        self.db.add(mapping)
        await self.db.commit()
        await self.db.refresh(mapping)
        return mapping

    async def get_file_info_by_ids(self, file_ids: list[str]) -> list[FileMapping]:
        if not file_ids:
            return []
        result = await self.db.execute(select(FileMapping).where(FileMapping.file_id.in_(file_ids)))
        return list(result.scalars().all())

    async def get_file_mapping_by_file_id(self, file_id: str) -> dict | None:
        result = await self.db.execute(select(FileMapping).where(FileMapping.file_id == file_id))
        mapping = result.scalar_one_or_none()
        return mapping.to_dict() if mapping else None

    async def list_by_user(self, user_id: int, *, offset: int = 0, limit: int = 20) -> list[FileMapping]:
        result = await self.db.execute(
            select(FileMapping).where(FileMapping.upload_user_id == user_id).order_by(FileMapping.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def get(self, id: int) -> FileMapping | None:
        return await self.db.get(FileMapping, id)

    async def delete(self, id: int) -> bool:
        obj = await self.get(id)
        if not obj:
            return False
        await self.db.delete(obj)
        await self.db.commit()
        return True

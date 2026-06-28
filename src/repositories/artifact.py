"""
业务产物 Repository。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.artifact import Artifact


class ArtifactRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(
        self,
        *,
        artifact_id: str,
        user_id: int,
        conversation_id: int,
        type: str,
        title: str,
        run_id: str | None = None,
        message_id: int | None = None,
        source_step_id: int | None = None,
        data: dict | None = None,
        text_content: str | None = None,
        file_id: str | None = None,
        status: str = "pending",
    ) -> Artifact:
        now = datetime.now(timezone.utc)
        art = Artifact(
            artifact_id=artifact_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
            source_step_id=source_step_id,
            type=type,
            title=title,
            status=status,
            data=data or {},
            text_content=text_content,
            file_id=file_id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(art)
        await self.db.commit()
        await self.db.refresh(art)
        return art

    async def get_by_artifact_id(self, artifact_id: str) -> Artifact | None:
        result = await self.db.execute(
            select(Artifact).where(Artifact.artifact_id == artifact_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, artifact_id: int) -> Artifact | None:
        return await self.db.get(Artifact, artifact_id)

    async def mark_ready(self, art: Artifact) -> Artifact:
        art.status = "ready"
        art.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(art)
        return art

    async def mark_failed(self, art: Artifact, *, error: str) -> Artifact:
        art.status = "failed"
        art.error = error
        art.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(art)
        return art

    async def soft_delete(self, art: Artifact) -> Artifact:
        art.status = "deleted"
        art.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(art)
        return art

    async def list_by_conversation(
        self,
        *,
        conversation_id: int,
        user_id: int,
        type: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[int, list[Artifact]]:
        base = select(Artifact).where(
            Artifact.conversation_id == conversation_id,
            Artifact.user_id == user_id,
            Artifact.status != "deleted",
        )
        if type:
            base = base.where(Artifact.type == type)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        rows = await self.db.execute(
            base.order_by(Artifact.created_at.desc()).offset(offset).limit(limit)
        )
        return total, list(rows.scalars().all())

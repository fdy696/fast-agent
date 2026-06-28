"""
产物服务 —— 旅行计划、报告、结构化 JSON、文件引用。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from repositories import ArtifactRepository


class ArtifactService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ArtifactRepository(db)

    async def create(
        self,
        *,
        user_id: int,
        conversation_id: int,
        type: str,
        title: str,
        run_id: str | None = None,
        data: dict | None = None,
        text_content: str | None = None,
    ) -> dict:
        art = await self.repo.insert(
            artifact_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
            type=type,
            title=title,
            run_id=run_id,
            data=data,
            text_content=text_content,
            status="pending",
        )
        return {
            "artifact_id": art.artifact_id,
            "type": art.type,
            "title": art.title,
            "status": art.status,
        }

    async def list_by_conversation(
        self, *, conversation_id: int, user_id: int, type: str | None = None
    ) -> list[dict]:
        _, arts = await self.repo.list_by_conversation(
            conversation_id=conversation_id, user_id=user_id, type=type
        )
        return [
            {
                "artifact_id": a.artifact_id,
                "type": a.type,
                "title": a.title,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in arts
        ]

    async def mark_ready(self, *, artifact_id: str) -> None:
        art = await self.repo.get_by_artifact_id(artifact_id)
        if art:
            await self.repo.mark_ready(art)

    async def mark_failed(self, *, artifact_id: str, error: str) -> None:
        art = await self.repo.get_by_artifact_id(artifact_id)
        if art:
            await self.repo.mark_failed(art, error=error)

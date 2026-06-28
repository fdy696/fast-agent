"""
Agent Run 状态机 Repository。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_run import AgentRun


class AgentRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        run_id: str,
        conversation_id: int,
        user_id: int,
        model: str,
        user_message_id: int | None = None,
    ) -> AgentRun:
        now = datetime.now(timezone.utc)
        run = AgentRun(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message_id=user_message_id,
            model=model,
            status="running",
            created_at=now,
            started_at=now,
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def get_by_run_id(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun).where(AgentRun.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def mark_succeeded(
        self,
        run: AgentRun,
        *,
        assistant_message_id: int | None = None,
    ) -> AgentRun:
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        if assistant_message_id is not None:
            run.assistant_message_id = assistant_message_id
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def mark_failed(self, run: AgentRun, *, error: str) -> AgentRun:
        run.status = "failed"
        run.error = error
        run.finished_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def mark_cancelled(self, run: AgentRun) -> AgentRun:
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def list_by_conversation(
        self,
        *,
        conversation_id: int,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[int, list[AgentRun]]:
        base = select(AgentRun).where(AgentRun.conversation_id == conversation_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        rows = await self.db.execute(
            base.order_by(AgentRun.created_at.desc()).offset(offset).limit(limit)
        )
        return total, list(rows.scalars().all())

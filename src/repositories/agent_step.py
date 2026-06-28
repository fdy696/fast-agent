"""
Agent Step Repository —— 记录逻辑阶段。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_step import AgentStep


class AgentStepRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(
        self,
        *,
        run_id: str,
        step_index: int,
        category: str,
        type: str,
        name: str,
        input: dict | None = None,
        output: dict | None = None,
        status: str = "running",
        error: str | None = None,
    ) -> AgentStep:
        now = datetime.now(timezone.utc)
        step = AgentStep(
            run_id=run_id,
            step_index=step_index,
            category=category,
            type=type,
            name=name,
            input=input,
            output=output,
            status=status,
            error=error,
            created_at=now,
            started_at=now,
        )
        self.db.add(step)
        await self.db.commit()
        await self.db.refresh(step)
        return step

    async def mark_done(
        self,
        step: AgentStep,
        *,
        status: str,
        output: dict | None = None,
        error: str | None = None,
    ) -> AgentStep:
        step.status = status
        step.finished_at = datetime.now(timezone.utc)
        if output is not None:
            step.output = output
        if error is not None:
            step.error = error
        await self.db.commit()
        await self.db.refresh(step)
        return step

    async def list_by_run(self, *, run_id: str) -> list[AgentStep]:
        result = await self.db.execute(
            select(AgentStep)
            .where(AgentStep.run_id == run_id)
            .order_by(AgentStep.step_index.asc())
        )
        return list(result.scalars().all())

    async def count_by_run(self, *, run_id: str) -> int:
        result = await self.db.execute(
            select(func.count()).where(AgentStep.run_id == run_id)
        )
        return result.scalar_one()

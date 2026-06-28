"""
Token 用量 Repository —— 成本账本。
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.token_usage import TokenUsage


class TokenUsageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(
        self,
        *,
        user_id: int,
        conversation_id: int,
        run_id: str,
        model: str,
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: Decimal | float = Decimal("0"),
    ) -> TokenUsage:
        usage = TokenUsage(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            model=model,
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=Decimal(str(cost)),
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(usage)
        await self.db.commit()
        await self.db.refresh(usage)
        return usage

    async def sum_cost_today(self, *, user_id: int) -> float:
        """今日累计费用。"""
        from datetime import date

        today = date.today()
        tz = timezone.utc
        start = datetime(today.year, today.month, today.day, tzinfo=tz)
        end = datetime(today.year, today.month, today.day, 23, 59, 59, 999999, tzinfo=tz)

        result = await self.db.execute(
            select(func.coalesce(func.sum(TokenUsage.cost), 0.0)).where(
                TokenUsage.user_id == user_id,
                TokenUsage.created_at >= start,
                TokenUsage.created_at <= end,
            )
        )
        return float(result.scalar_one())

    async def list_by_user(
        self,
        *,
        user_id: int,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[int, list[TokenUsage]]:
        base = select(TokenUsage).where(TokenUsage.user_id == user_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        rows = await self.db.execute(
            base.order_by(TokenUsage.created_at.desc()).offset(offset).limit(limit)
        )
        return total, list(rows.scalars().all())

    async def list_by_run(self, *, run_id: str) -> list[TokenUsage]:
        result = await self.db.execute(
            select(TokenUsage)
            .where(TokenUsage.run_id == run_id)
            .order_by(TokenUsage.created_at.desc())
        )
        return list(result.scalars().all())

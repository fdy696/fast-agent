"""Ownership-safe Redis lock for rolling-summary jobs."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class SummaryLock:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(session_id: str) -> str:
        return f"chat:summary:{session_id}"

    async def try_acquire(self, session_id: str) -> str | None:
        token = uuid.uuid4().hex
        acquired = await self.redis.set(
            self.key(session_id), token, nx=True, ex=self.ttl_seconds
        )
        return token if acquired else None

    async def release(self, session_id: str, token: str) -> bool:
        result = await self.redis.eval(
            _RELEASE_SCRIPT, 1, self.key(session_id), token
        )
        return bool(result)

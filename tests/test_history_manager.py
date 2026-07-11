from unittest.mock import AsyncMock

from services.summary_service import build_summary_messages
from utils.summary_lock import SummaryLock


async def test_summary_lock_releases_only_with_owner_token():
    redis = AsyncMock()
    redis.set.return_value = True
    redis.eval.return_value = 1
    lock = SummaryLock(redis, ttl_seconds=600)

    token = await lock.try_acquire("session-1")
    assert token
    await lock.release("session-1", token)

    redis.set.assert_awaited_once_with(
        "chat:summary:session-1", token, nx=True, ex=600
    )
    redis.eval.assert_awaited_once()
    assert redis.eval.await_args.args[-1] == token


def test_summary_is_stored_as_valid_model_history():
    messages = build_summary_messages("用户喜欢自然风景。")
    assert messages[0].parts[0].content.endswith("用户喜欢自然风景。")

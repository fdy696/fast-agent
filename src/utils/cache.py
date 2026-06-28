import json
from typing import Any

import redis.asyncio as redis

from core.config import settings
from log import logger


class CacheManager:
    def __init__(self):
        self.redis: redis.Redis | None = None

    async def connect(self):
        if self.redis is None:
            try:
                self.redis = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=20, retry_on_timeout=True)
                await self.redis.ping()
                logger.info("Redis连接成功")
            except Exception as e:
                logger.warning(f"Redis连接失败: {str(e)}，缓存功能将被禁用")
                self.redis = None

    async def disconnect(self):
        if self.redis:
            await self.redis.close()
            self.redis = None
            logger.info("Redis连接已断开")

    async def get(self, key: str) -> Any | None:
        if not self.redis:
            return None
        try:
            data = await self.redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"获取缓存失败 key={key}: {str(e)}")
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        if not self.redis:
            return False
        try:
            await self.redis.setex(key, ttl or settings.CACHE_TTL, json.dumps(value, ensure_ascii=False, default=str))
            return True
        except Exception as e:
            logger.error(f"设置缓存失败 key={key}: {str(e)}")
            return False

    async def delete(self, key: str) -> bool:
        if not self.redis:
            return False
        try:
            return bool(await self.redis.delete(key))
        except Exception as e:
            logger.error(f"删除缓存失败 key={key}: {str(e)}")
            return False

    async def clear_pattern(self, pattern: str) -> int:
        if not self.redis:
            return 0
        try:
            keys = await self.redis.keys(pattern)
            return await self.redis.delete(*keys) if keys else 0
        except Exception as e:
            logger.error(f"批量删除缓存失败 pattern={pattern}: {str(e)}")
            return 0

    def cache_key(self, prefix: str, *args, **kwargs) -> str:
        key_parts = [prefix]
        key_parts.extend(str(arg) for arg in args)
        key_parts.extend(f"{k}:{v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)


cache_manager = CacheManager()


async def clear_user_cache(user_id: int):
    patterns = [f"user:{user_id}:*", f"userinfo:{user_id}"]
    total_cleared = 0
    for pattern in patterns:
        total_cleared += await cache_manager.clear_pattern(pattern)
    logger.info(f"清除用户{user_id}相关缓存，共{total_cleared}个键")
    return total_cleared

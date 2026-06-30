import json
from loguru import logger
import redis.asyncio as aioredis
from app.config import settings


class ShortTermMemory:
    """Redis asosida qisqa muddatli suhbat xotirasi."""

    PREFIX = "chat_history"
    TYPING_PREFIX = "typing"

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def _key(self, user_id: int) -> str:
        return f"{self.PREFIX}:{user_id}"

    async def add_message(
        self,
        user_id: int,
        role: str,
        content: str,
        max_messages: int | None = None,
    ) -> None:
        r = await self._get_redis()
        key = self._key(user_id)
        message = json.dumps({"role": role, "content": content})
        await r.rpush(key, message)
        await r.expire(key, settings.redis_ttl_seconds)

        limit = max_messages or settings.agent_max_context_messages
        length = await r.llen(key)
        if length > limit:
            trim_from = length - limit
            await r.ltrim(key, trim_from, -1)

    async def get_history(self, user_id: int) -> list[dict]:
        r = await self._get_redis()
        key = self._key(user_id)
        raw_messages = await r.lrange(key, 0, -1)
        messages = []
        for raw in raw_messages:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning(f"Redis: yaroqsiz xabar o'tkazib yuborildi: {raw[:50]}")
        return messages

    async def get_recent(self, user_id: int, n: int = 10) -> list[dict]:
        r = await self._get_redis()
        key = self._key(user_id)
        raw_messages = await r.lrange(key, -n, -1)
        messages = []
        for raw in raw_messages:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
        return messages

    async def count(self, user_id: int) -> int:
        r = await self._get_redis()
        return await r.llen(self._key(user_id))

    async def clear(self, user_id: int) -> None:
        r = await self._get_redis()
        await r.delete(self._key(user_id))
        logger.info(f"Foydalanuvchi {user_id} Redis xotirasi tozalandi")

    async def set_agent_enabled(self, enabled: bool) -> None:
        r = await self._get_redis()
        await r.set("agent:enabled", "1" if enabled else "0")

    async def is_agent_enabled(self) -> bool:
        r = await self._get_redis()
        val = await r.get("agent:enabled")
        if val is None:
            return settings.agent_enabled
        return val == "1"

    async def set_user_paused(self, user_id: int, paused: bool) -> None:
        r = await self._get_redis()
        key = f"paused:{user_id}"
        if paused:
            await r.set(key, "1", ex=settings.redis_ttl_seconds)
        else:
            await r.delete(key)

    async def is_user_paused(self, user_id: int) -> bool:
        r = await self._get_redis()
        return bool(await r.get(f"paused:{user_id}"))

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()


short_term_memory = ShortTermMemory()

"""Butun ilova uchun umumiy Redis clienti (bitta ulanish puli, lazy-init).

Ilgari har bir modul `short_term_memory._get_redis()` (private metod) orqali
to'g'ridan-to'g'ri xotira qatlamiga kirar edi — bu inkapsulyatsiyani buzardi.
Endi Redis kerak bo'lgan istalgan joy shu modulni ishlatadi.
"""
import redis.asyncio as aioredis
from app.config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None

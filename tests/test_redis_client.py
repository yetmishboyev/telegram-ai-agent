"""Umumiy Redis accessor (`app.database.redis`) testlari — roadmap Faza 4, band 12."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.database.redis as redis_module


@pytest.mark.asyncio
async def test_get_redis_returns_same_client_on_repeated_calls():
    original = redis_module._redis
    redis_module._redis = None
    try:
        with patch("app.database.redis.aioredis.from_url") as mock_from_url:
            mock_from_url.return_value = MagicMock()
            first = await redis_module.get_redis()
            second = await redis_module.get_redis()

        assert first is second
        mock_from_url.assert_called_once()
    finally:
        redis_module._redis = original


@pytest.mark.asyncio
async def test_close_redis_resets_singleton_and_calls_aclose():
    original = redis_module._redis
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()
    redis_module._redis = fake_client
    try:
        await redis_module.close_redis()
        assert redis_module._redis is None
        fake_client.aclose.assert_called_once()
    finally:
        redis_module._redis = original


@pytest.mark.asyncio
async def test_close_redis_is_safe_when_never_connected():
    original = redis_module._redis
    redis_module._redis = None
    try:
        await redis_module.close_redis()  # xato ko'tarmasligi kerak
        assert redis_module._redis is None
    finally:
        redis_module._redis = original

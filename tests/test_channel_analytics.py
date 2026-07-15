"""Kanal analitika (timeline, tur samaradorligi, o'sish strategiyasi) testlari."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.api.routes.channel import (
    get_channel_timeline, get_type_performance, get_growth_strategy,
    STRATEGY_CACHE_KEY,
)
from app.services.news_fetcher import news_fetcher
from app.database.redis import get_redis


@pytest.mark.asyncio
async def test_analytics_endpoints_require_auth():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/api/channel/timeline", "/api/channel/type-performance", "/api/channel/strategy"):
            resp = await client.get(path)
            assert resp.status_code == 401, path


@pytest.mark.asyncio
async def test_timeline_fills_empty_days(db_session):
    data = await get_channel_timeline(days=7, db=db_session, _=None)
    assert data["days"] == 7
    assert len(data["series"]) == 7  # har kun bor, bo'shlari 0
    for d in data["series"]:
        assert set(d.keys()) == {"date", "posts", "views"}


@pytest.mark.asyncio
async def test_timeline_clamps_days(db_session):
    data = await get_channel_timeline(days=9999, db=db_session, _=None)
    assert data["days"] == 180
    data = await get_channel_timeline(days=-5, db=db_session, _=None)
    assert data["days"] == 1


@pytest.mark.asyncio
async def test_type_performance_shape(db_session):
    rows = await get_type_performance(db=db_session, _=None)
    assert isinstance(rows, list)
    for r in rows:
        assert set(r.keys()) == {"post_type", "posts", "avg_views", "total_views"}


@pytest.mark.asyncio
async def test_generate_growth_strategy_parses_and_caps():
    fake = json.dumps({
        "holat": "Kanal o'sish bosqichida.",
        "tavsiyalar": [f"tavsiya {i}" for i in range(10)],  # 6 tadan ko'p
        "kontent_goyalar": ["g'oya 1", "g'oya 2"],
        "keyingi_qadam": "Quiz e'lon qilish",
    })
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value=fake)):
        s = await news_fetcher.generate_growth_strategy({"umumiy": {"total_posts": 5}})
    assert s["holat"] == "Kanal o'sish bosqichida."
    assert len(s["tavsiyalar"]) == 6  # cheklangan
    assert s["keyingi_qadam"] == "Quiz e'lon qilish"


@pytest.mark.asyncio
async def test_generate_growth_strategy_none_on_bad_json():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value="json emas")):
        s = await news_fetcher.generate_growth_strategy({})
    assert s is None


@pytest.mark.asyncio
async def test_strategy_endpoint_uses_cache(db_session):
    r = await get_redis()
    payload = {"generated_at": "2026-07-16T00:00:00", "holat": "keshdan",
               "tavsiyalar": [], "kontent_goyalar": [], "keyingi_qadam": "x"}
    await r.setex(STRATEGY_CACHE_KEY, 60, json.dumps(payload, ensure_ascii=False))
    try:
        with patch("app.services.news_fetcher.news_fetcher.generate_growth_strategy",
                   AsyncMock(side_effect=AssertionError("kesh borida LLM chaqirilmasligi kerak"))):
            data = await get_growth_strategy(db=db_session, _=None)
        assert data["cached"] is True
        assert data["holat"] == "keshdan"
    finally:
        await r.delete(STRATEGY_CACHE_KEY)

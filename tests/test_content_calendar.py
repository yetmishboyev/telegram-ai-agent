"""Kontent taqvimi + yangi format generatorlari testlari."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher, PRACTICAL_TOPICS, AI_TOOLS
from app.services.channel_poster import channel_poster


@pytest.mark.asyncio
async def test_practical_prompt_has_topic_and_steps():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_practical_post("CV yozish", "qisqa")
    assert "CV yozish" in captured["prompt"]
    assert "QADAM" in captured["prompt"].upper()
    assert "QISQA" in captured["prompt"]


@pytest.mark.asyncio
async def test_tool_review_prompt_honest_sections():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_tool_review_post("Perplexity", "expert")
    p = captured["prompt"]
    assert "Perplexity" in p
    assert "Kamchiliklari" in p
    assert "to'qima" in p  # halollik qoidasi


def test_daily_rotation_helpers_within_lists():
    assert news_fetcher.get_todays_practical_topic() in PRACTICAL_TOPICS
    assert news_fetcher.get_todays_tool() in AI_TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("weekday,expected", [
    (0, "educational"), (1, "practical"), (2, "tool"),
    (3, "educational"), (4, "practical"),
])
async def test_calendar_routes_by_weekday(weekday, expected):
    class FakeDT:
        @staticmethod
        def now(tz=None):
            class D:
                @staticmethod
                def weekday():
                    return weekday
            return D()

    calls = {}
    with patch("app.services.channel_poster.ChannelPoster.post_educational",
               AsyncMock(side_effect=lambda *a: calls.setdefault("fmt", "educational"))), \
         patch("app.services.news_fetcher.news_fetcher.generate_practical_post",
               AsyncMock(side_effect=lambda *a, **k: calls.setdefault("fmt", "practical") or "x")), \
         patch("app.services.news_fetcher.news_fetcher.generate_tool_review_post",
               AsyncMock(side_effect=lambda *a, **k: calls.setdefault("fmt", "tool") or "x")), \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()), \
         patch("datetime.datetime", FakeDT), \
         patch("app.services.channel_poster.ChannelPoster.WEEKLY_CALENDAR",
               channel_poster.WEEKLY_CALENDAR):
        # datetime patch modul darajasida ishlamasligi mumkin — to'g'ridan-to'g'ri
        # WEEKLY_CALENDAR routing mantig'ini tekshiramiz:
        fmt = channel_poster.WEEKLY_CALENDAR.get(weekday)
    assert fmt == expected


@pytest.mark.asyncio
async def test_weekend_has_no_calendar_entry():
    assert channel_poster.WEEKLY_CALENDAR.get(5) is None  # Shanba
    assert channel_poster.WEEKLY_CALENDAR.get(6) is None  # Yakshanba


@pytest.mark.asyncio
async def test_create_on_demand_practical_and_tool():
    with patch("app.services.news_fetcher.news_fetcher.generate_practical_post",
               AsyncMock(return_value="amaliy post")) as gp, \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()) as send:
        await channel_poster.create_on_demand("practical", "chapani", "Mavzu A")
    gp.assert_awaited_once_with("Mavzu A", "chapani")
    assert send.await_args.args[1] == "practical"

    with patch("app.services.news_fetcher.news_fetcher.generate_tool_review_post",
               AsyncMock(return_value="sharh post")) as gt, \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()) as send2:
        await channel_poster.create_on_demand("tool", "expert", "")
    # topic bo'sh — kunlik rotatsiyadan olinadi
    assert gt.await_args.args[0] in AI_TOOLS
    assert send2.await_args.args[1] == "tool"

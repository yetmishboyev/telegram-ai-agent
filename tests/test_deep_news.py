"""Yangilik curation + chuqur tahlil posti testlari."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher
from app.services.channel_poster import channel_poster


def _llm(value):
    return patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value=value))


_ITEMS = [
    {"title": "OpenAI releases GPT-6", "desc": "Big model", "link": "http://a"},
    {"title": "Meta open-sources Llama 5", "desc": "Open weights", "link": "http://b"},
    {"title": "Minor AI startup raises seed", "desc": "Funding", "link": "http://c"},
]


@pytest.mark.asyncio
async def test_curate_picks_indexed_item():
    with _llm(json.dumps({"index": 1, "reason": "ochiq model — hamma uchun"})):
        picked = await news_fetcher.curate_top_news(_ITEMS)
    assert picked["item"]["title"] == "Meta open-sources Llama 5"
    assert "ochiq" in picked["reason"]


@pytest.mark.asyncio
async def test_curate_out_of_range_index_falls_back_to_first():
    with _llm(json.dumps({"index": 99, "reason": "x"})):
        picked = await news_fetcher.curate_top_news(_ITEMS)
    assert picked["item"]["title"] == _ITEMS[0]["title"]


@pytest.mark.asyncio
async def test_curate_empty_and_single():
    assert await news_fetcher.curate_top_news([]) is None
    # bitta element — LLM chaqirilmaydi
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(side_effect=AssertionError("chaqirilmasligi kerak"))):
        picked = await news_fetcher.curate_top_news([_ITEMS[0]])
    assert picked["item"] == _ITEMS[0]


@pytest.mark.asyncio
async def test_curate_llm_error_falls_back_to_first():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(side_effect=RuntimeError("down"))):
        picked = await news_fetcher.curate_top_news(_ITEMS)
    assert picked["item"] == _ITEMS[0]


@pytest.mark.asyncio
async def test_deep_news_prompt_contains_item_and_style():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post matni"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_deep_news_post(_ITEMS[0], "expert", "muhim sabab")
    p = captured["prompt"]
    assert "OpenAI releases GPT-6" in p
    assert "EKSPERT" in p
    assert "muhim sabab" in p
    assert "BITTA yangilik" in p


@pytest.mark.asyncio
async def test_build_deep_news_flow():
    """post_news oqimi: keng ro'yxat → curation → deep post → (matn, mavzu)."""
    with patch("app.services.news_fetcher.news_fetcher.get_ai_news", AsyncMock(return_value=_ITEMS)), \
         patch("app.services.news_fetcher.news_fetcher.curate_top_news",
               AsyncMock(return_value={"item": _ITEMS[1], "reason": "r"})) as cur, \
         patch("app.services.news_fetcher.news_fetcher.generate_deep_news_post",
               AsyncMock(return_value="chuqur post")) as gen:
        text, topic = await channel_poster._build_deep_news("chapani")

    assert text == "chuqur post"
    assert topic == "Meta open-sources Llama 5"
    cur.assert_awaited_once_with(_ITEMS)
    assert gen.await_args.kwargs.get("curation_reason") == "r"


@pytest.mark.asyncio
async def test_build_deep_news_none_when_no_items():
    with patch("app.services.news_fetcher.news_fetcher.get_ai_news", AsyncMock(return_value=[])):
        text, topic = await channel_poster._build_deep_news("chapani")
    assert text is None

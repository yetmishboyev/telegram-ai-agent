"""Kanal posti uslub tizimi va on-demand yaratish testlari."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.news_fetcher import (
    news_fetcher, style_instruction, POST_STYLES, DEFAULT_STYLE,
)
from app.services.channel_poster import channel_poster


def test_style_instruction_known_and_default():
    assert "CHAPANI" in style_instruction("chapani")
    assert "QISQA" in style_instruction("qisqa")
    # noma'lum uslub → default
    assert style_instruction("yoq-bunday") == POST_STYLES[DEFAULT_STYLE]["instruction"]


@pytest.mark.asyncio
async def test_educational_post_includes_style_instruction():
    captured = {}

    async def fake_call(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch.object(news_fetcher, "_call_llm", AsyncMock(side_effect=fake_call)):
        await news_fetcher.generate_educational_post("LLM nima", "chapani")
    assert "CHAPANI" in captured["prompt"]
    assert "LLM nima" in captured["prompt"]


@pytest.mark.asyncio
async def test_free_post_uses_style_and_topic():
    captured = {}

    async def fake_call(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch.object(news_fetcher, "_call_llm", AsyncMock(side_effect=fake_call)):
        await news_fetcher.generate_free_post("Biznes avtomatlashtirish", "qisqa")
    assert "QISQA" in captured["prompt"]
    assert "Biznes avtomatlashtirish" in captured["prompt"]


@pytest.mark.asyncio
async def test_create_on_demand_threads_style_to_approval():
    with patch("app.services.news_fetcher.news_fetcher.generate_free_post",
               AsyncMock(return_value="matn")) as gen, \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()) as send:
        await channel_poster.create_on_demand("free", "chapani", "Mavzu")

    gen.assert_awaited_once()
    assert gen.await_args.args == ("Mavzu", "chapani")
    send.assert_awaited_once()
    # _send_for_approval(text, post_type, topic, style)
    assert send.await_args.args[1] == "free"
    assert send.await_args.args[3] == "chapani"

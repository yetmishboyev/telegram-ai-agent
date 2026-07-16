"""Kanal posti uslub tizimi va on-demand yaratish testlari."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.news_fetcher import (
    news_fetcher, style_instruction, POST_STYLES, DEFAULT_STYLE,
)
from app.services.channel_poster import channel_poster
from app.utils.uz_text import to_latin_uz


def test_style_instruction_known_and_default():
    assert "CHAPANI" in style_instruction("chapani")
    assert "QISQA" in style_instruction("qisqa")
    assert "JONLI-PROFESSIONAL" in style_instruction("jonli")
    # noma'lum uslub → default (lotin qoidasi qo'shilgan holda)
    assert style_instruction("yoq-bunday").startswith(POST_STYLES[DEFAULT_STYLE]["instruction"])
    # har bir uslubga lotin-only qoidasi qo'shiladi
    assert "LOTIN" in style_instruction("chapani")


def test_default_style_is_jonli_professional():
    # Ega chapani juda topvaroq deb topdi — jadval postlari jonli-professional
    assert DEFAULT_STYLE == "jonli"
    assert '"siz"' in POST_STYLES["jonli"]["instruction"]


def test_to_latin_uz_fixes_mixed_script():
    assert to_latin_uz("qidirади") == "qidiradi"      # ради krill
    assert to_latin_uz("Шунday") == "Shunday"
    assert to_latin_uz("ў ғ қ ҳ") == "o' g' q h"
    assert to_latin_uz("oddiy lotin matn") == "oddiy lotin matn"  # o'zgarmaydi


@pytest.mark.asyncio
async def test_news_fetcher_transliterates_llm_output():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(return_value="AI Agent o'zi qidиради")):
        r = await news_fetcher._call_llm(messages=[{"role": "user", "content": "x"}])
    assert r == "AI Agent o'zi qidiradi"
    assert not any("а" <= ch <= "я" for ch in r)  # krill qolmadi


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

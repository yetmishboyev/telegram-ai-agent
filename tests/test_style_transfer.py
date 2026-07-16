"""Namuna kanaldan uslub o'rganish (style transfer) testlari."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher, style_instruction
from app.services.channel_poster import channel_poster


@pytest.fixture(autouse=True)
def _reset_learned_cache():
    """Har testdan keyin learned-kesh tozalanadi (testlar bir-biriga ta'sir qilmasin)."""
    yield
    news_fetcher.set_learned_style_cache(None)


@pytest.mark.asyncio
async def test_style_text_learned_includes_card_and_samples():
    news_fetcher.set_learned_style_cache({
        "source": "@namuna",
        "style_card": "Qisqa gaplar bilan yoz. Har postni savol bilan boshla.",
        "samples": ["Birinchi namuna post matni.", "Ikkinchi namuna post matni."],
    })
    text = await news_fetcher._style_text("learned")
    assert "O'RGANILGAN" in text
    assert "@namuna" in text
    assert "savol bilan boshla" in text
    assert "Birinchi namuna" in text
    assert "KO'CHIRMA" in text  # plagiat himoyasi
    assert "LOTIN" in text


@pytest.mark.asyncio
async def test_style_text_learned_missing_falls_back_to_default():
    news_fetcher.set_learned_style_cache(None)
    with patch.object(news_fetcher, "get_learned_style", AsyncMock(return_value=None)):
        text = await news_fetcher._style_text("learned")
    assert text == style_instruction("learned")  # default (chapani) instruktsiya


@pytest.mark.asyncio
async def test_generator_threads_learned_style_into_prompt():
    news_fetcher.set_learned_style_cache({
        "source": "@namuna", "style_card": "NOYOB-USLUB-BELGISI", "samples": [],
    })
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_educational_post("RAG", "learned")
    assert "NOYOB-USLUB-BELGISI" in captured["prompt"]


def _fake_msgs():
    """Views har xil, ba'zilari qisqa/bo'sh — filtr sinovi uchun."""
    def m(text, views):
        return SimpleNamespace(text=text, views=views)
    long = "Bu yetarlicha uzun namuna post matni. " * 10  # ~390 belgi
    return [
        m(long + "ENG-TOP", 900),
        m(long + "IKKINCHI", 500),
        m("qisqa post", 9999),          # <200 belgi — filtrlanadi
        m(None, 100),                    # matnsiz — filtrlanadi
        m(long + "UCHINCHI", 300),
        m(long + "D", 200), m(long + "E", 150), m(long + "F", 100),
    ]


@pytest.mark.asyncio
async def test_learn_style_from_channel_saves_and_caches(db_session):
    with patch("app.services.telegram_service.telegram_service._client") as client, \
         patch("app.services.news_fetcher.news_fetcher.analyze_style",
               AsyncMock(return_value="USLUB KARTASI MATNI")):
        client.get_messages = AsyncMock(return_value=_fake_msgs())
        result = await channel_poster.learn_style_from_channel("https://t.me/birfoizbilim")

    assert result is not None
    assert result["source"] == "@birfoizbilim"  # link normalizatsiyasi
    assert result["samples_count"] == 3

    # Kesh yangilangan — eng ko'p ko'rilgan post birinchi namuna
    learned = await news_fetcher.get_learned_style()
    assert learned["style_card"] == "USLUB KARTASI MATNI"
    assert "ENG-TOP" in learned["samples"][0]

    # DB'da saqlangan — tozalaymiz
    from sqlalchemy import select, delete
    from app.database.models import AgentConfig
    r = await db_session.execute(select(AgentConfig).where(AgentConfig.key == "learned_style"))
    assert r.scalar_one_or_none() is not None
    await db_session.execute(delete(AgentConfig).where(AgentConfig.key == "learned_style"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_learn_style_strips_channel_signatures(db_session):
    """Namunalardagi @username imzolar tozalanadi — model ko'chirmasligi uchun."""
    def m(text, views):
        return SimpleNamespace(text=text, views=views)

    body = "Bu yetarlicha uzun namuna post matni bo'lib turibdi. " * 8
    msgs = [m(body + f"MATN{i}\n\n@birfoizbilim", 100 - i) for i in range(6)]

    with patch("app.services.telegram_service.telegram_service._client") as client, \
         patch("app.services.news_fetcher.news_fetcher.analyze_style",
               AsyncMock(return_value="karta")):
        client.get_messages = AsyncMock(return_value=msgs)
        result = await channel_poster.learn_style_from_channel("@birfoizbilim")

    assert result is not None
    learned = await news_fetcher.get_learned_style()
    for s in learned["samples"]:
        assert "@birfoizbilim" not in s

    from sqlalchemy import delete
    from app.database.models import AgentConfig
    await db_session.execute(delete(AgentConfig).where(AgentConfig.key == "learned_style"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_call_llm_strips_source_mention_from_output():
    """Model namuna kanal imzosini qo'shsa ham, natijadan deterministik olib tashlanadi."""
    news_fetcher.set_learned_style_cache({"source": "@birfoizbilim", "style_card": "x", "samples": []})
    fake_out = "**Sarlavha**\n\nPost matni shu yerda.\n\n@birfoizbilim\n\n#AI"
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value=fake_out)):
        result = await news_fetcher._call_llm(messages=[{"role": "user", "content": "x"}])
    assert "@birfoizbilim" not in result
    assert "Post matni shu yerda." in result
    assert "#AI" in result


@pytest.mark.asyncio
async def test_learn_style_needs_enough_posts():
    few = _fake_msgs()[:3]  # filtrdan keyin 2 ta qoladi (<5)
    with patch("app.services.telegram_service.telegram_service._client") as client:
        client.get_messages = AsyncMock(return_value=few)
        result = await channel_poster.learn_style_from_channel("@kichikkanal")
    assert result is None

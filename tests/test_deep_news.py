"""Yangilik curation + chuqur tahlil posti testlari."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.news_fetcher import (
    news_fetcher, NEWS_POOL_SIZE, NEWS_FALLBACK_CATEGORY,
)
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
    """post_news oqimi: keng ro'yxat → tarix → curation → (matn, mavzu, kategoriya)."""
    history = [{"topic": "Eski yangilik", "category": "xavfsizlik"}]
    with patch("app.services.news_fetcher.news_fetcher.get_ai_news",
               AsyncMock(return_value=_ITEMS)) as news, \
         patch.object(channel_poster, "_recent_news_posts", AsyncMock(return_value=history)), \
         patch("app.services.news_fetcher.news_fetcher.curate_top_news",
               AsyncMock(return_value={"item": _ITEMS[1], "reason": "r",
                                       "category": "mahsulot"})) as cur, \
         patch("app.services.news_fetcher.news_fetcher.generate_deep_news_post",
               AsyncMock(return_value="chuqur post")) as gen:
        text, topic, category, source = await channel_poster._build_deep_news("chapani")

    assert text == "chuqur post"
    assert topic == "Meta open-sources Llama 5"
    assert category == "mahsulot"
    assert source == _ITEMS[1]["link"]      # manba havolasi postga chiqadi
    # Nomzod hovuzi kengaytirilgan (ilgari 10 edi) va tarix curation'ga uzatiladi
    assert news.await_args.kwargs["count"] == NEWS_POOL_SIZE
    cur.assert_awaited_once_with(_ITEMS, recent=history)
    assert gen.await_args.kwargs.get("curation_reason") == "r"


@pytest.mark.asyncio
async def test_build_deep_news_none_when_no_items():
    with patch("app.services.news_fetcher.news_fetcher.get_ai_news", AsyncMock(return_value=[])), \
         patch.object(channel_poster, "_recent_news_posts", AsyncMock(return_value=[])):
        text, topic, category, source = await channel_poster._build_deep_news("chapani")
    assert text is None
    assert source == ""


@pytest.mark.asyncio
async def test_build_deep_news_survives_history_db_error():
    """Tarix o'qilmasa ham post tayyorlanadi (tarixsiz curation)."""
    with patch("app.services.news_fetcher.news_fetcher.get_ai_news", AsyncMock(return_value=_ITEMS)), \
         patch("app.database.session.AsyncSessionLocal",
               MagicMock(side_effect=RuntimeError("db yo'q"))), \
         patch("app.services.news_fetcher.news_fetcher.curate_top_news",
               AsyncMock(return_value={"item": _ITEMS[0], "reason": "", "category": "biznes"})), \
         patch("app.services.news_fetcher.news_fetcher.generate_deep_news_post",
               AsyncMock(return_value="post")):
        text, _, category, _src = await channel_poster._build_deep_news("jonli")
    assert text == "post"
    assert category == "biznes"


# ─── takrorlanishga qarshi qatlamlar ────────────────────────────────────────────

def test_is_repeat_detects_same_story_and_ignores_different():
    prev = ["OpenAI releases GPT-6"]
    # aynan o'zi va kichik farq bilan — takror
    assert news_fetcher._is_repeat("OpenAI releases GPT-6", prev) is True
    assert news_fetcher._is_repeat("OpenAI Releases GPT-6!", prev) is True
    # butunlay boshqa yangilik — takror emas
    assert news_fetcher._is_repeat("Meta open-sources Llama 5", prev) is False
    # tarix bo'sh bo'lsa hech narsa takror emas
    assert news_fetcher._is_repeat("OpenAI releases GPT-6", []) is False


@pytest.mark.asyncio
async def test_curate_drops_candidates_already_posted():
    """Oldin chiqqan yangilik LLM ga nomzod sifatida ham berilmaydi."""
    recent = [{"topic": "OpenAI releases GPT-6", "category": "mahsulot"}]
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return json.dumps({"index": 0, "category": "biznes", "reason": "r"})

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        picked = await news_fetcher.curate_top_news(_ITEMS, recent=recent)

    # Nomzodlar ro'yxatida yo'q (tarix blokida ko'rinishi — bu boshqa masala)
    listing = captured["prompt"].split("Quyidagi yangiliklar")[1].split("Tanlash qoidalari")[0]
    assert "OpenAI releases GPT-6" not in listing
    assert picked["item"]["title"] != "OpenAI releases GPT-6"


@pytest.mark.asyncio
async def test_curate_falls_back_when_every_candidate_is_repeat():
    """Hamma nomzod takror bo'lsa ham kanal postsiz qolmaydi."""
    recent = [{"topic": it["title"], "category": "mahsulot"} for it in _ITEMS]
    with _llm(json.dumps({"index": 2, "category": "biznes", "reason": "r"})):
        picked = await news_fetcher.curate_top_news(_ITEMS, recent=recent)
    assert picked is not None
    assert picked["item"] in _ITEMS


@pytest.mark.asyncio
async def test_curate_prompt_has_history_categories_and_diversity_rule():
    recent = [
        {"topic": "AI nazoratdan chiqdi", "category": "xavfsizlik"},
        {"topic": "Yana bir xavf", "category": "xavfsizlik"},
    ]
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return json.dumps({"index": 0, "category": "tadqiqot", "reason": "r"})

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.curate_top_news(_ITEMS, recent=recent)

    p = captured["prompt"]
    assert "AI nazoratdan chiqdi" in p          # tarix ko'rsatilgan
    assert "xavfsizlik" in p                     # kategoriya bilan
    assert "BOSHQA kategoriyadagi" in p          # xilma-xillik qoidasi
    assert "mahsulot" in p and "tadqiqot" in p   # katalog
    assert "TAKRORLAMA" in p


@pytest.mark.asyncio
async def test_curate_returns_category_and_rejects_unknown_one():
    with _llm(json.dumps({"index": 0, "category": "tadqiqot", "reason": "r"})):
        picked = await news_fetcher.curate_top_news(_ITEMS)
    assert picked["category"] == "tadqiqot"

    with _llm(json.dumps({"index": 0, "category": "allaqanday-narsa", "reason": "r"})):
        picked = await news_fetcher.curate_top_news(_ITEMS)
    assert picked["category"] == NEWS_FALLBACK_CATEGORY


# ─── manba havolasi ────────────────────────────────────────────────────────────
# Havolani MODEL emas, kod qo'shadi: model uzun URL'ni buzardi va muharrir
# qatlami uni "suv" deb olib tashlashi mumkin edi.

def test_source_html_renders_domain_as_link():
    from app.services.channel_poster import _source_html
    out = _source_html("https://www.techcrunch.com/2026/09/01/openai-ads/")
    assert 'href="https://www.techcrunch.com/2026/09/01/openai-ads/"' in out
    assert ">techcrunch.com<" in out       # www. olib tashlanadi
    assert out.startswith("\n\n🔗 Manba:")


def test_source_html_is_empty_without_url():
    from app.services.channel_poster import _source_html
    assert _source_html("") == ""
    assert _source_html("shunchaki-matn") == ""


def test_source_html_escapes_hostile_url():
    from app.services.channel_poster import _source_html
    out = _source_html('https://x.com/a"><script>alert(1)</script>')
    assert "<script>" not in out
    assert "&quot;" in out or "&#x27;" in out or "&lt;" in out


@pytest.mark.asyncio
async def test_approval_text_carries_source_above_the_footer():
    """Manba tahrirdan KEYIN qo'shiladi va Redisda ham saqlanadi."""
    import json
    from app.services.channel_poster import CHANNEL_FOOTER_HTML
    saved = {}

    class FakeRedis:
        async def setex(self, key, ttl, value):
            saved.update(json.loads(value))

    with patch("app.services.news_fetcher.news_fetcher.critique_and_improve",
               AsyncMock(side_effect=lambda t, k="": t)), \
         patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())), \
         patch("app.services.bot_service.bot_service",
               type("B", (), {"_client": AsyncMock()})):
        await channel_poster._send_for_approval(
            "Post matni", "news", "mavzu", "jonli", "mahsulot",
            "https://venturebeat.com/ai/story",
        )

    text = saved["text"]
    assert "venturebeat.com" in text
    assert text.index("venturebeat.com") < text.index(CHANNEL_FOOTER_HTML.strip()[:20])
    assert saved["source_url"] == "https://venturebeat.com/ai/story"


@pytest.mark.asyncio
async def test_non_news_post_has_no_source_line():
    import json
    saved = {}

    class FakeRedis:
        async def setex(self, key, ttl, value):
            saved.update(json.loads(value))

    with patch("app.services.news_fetcher.news_fetcher.critique_and_improve",
               AsyncMock(side_effect=lambda t, k="": t)), \
         patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())), \
         patch("app.services.bot_service.bot_service",
               type("B", (), {"_client": AsyncMock()})):
        await channel_poster._send_for_approval("Ta'limiy matn", "educational")

    assert "🔗 Manba" not in saved["text"]

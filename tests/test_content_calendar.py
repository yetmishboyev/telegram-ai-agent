"""Kontent taqvimi + yangi format generatorlari testlari."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import (
    news_fetcher, PRACTICAL_TOPICS, AI_TOOLS,
    EDUCATIONAL_FORMATS, EDUCATIONAL_TOPICS, PRACTICAL_FORMATS,
)
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
    assert news_fetcher.get_todays_educational_format() in EDUCATIONAL_FORMATS


# ─── ta'limiy postlar bir xil bo'lib qolmasligi ─────────────────────────────────

def test_educational_format_rotates_and_decorrelates_from_topic():
    """Janr har kuni almashadi va mavzu bilan qulflanib qolmaydi."""
    fmt_keys = list(EDUCATIONAL_FORMATS)
    pairs = []
    for ordinal in range(0, 60):
        topic = EDUCATIONAL_TOPICS[ordinal % len(EDUCATIONAL_TOPICS)]
        fmt = fmt_keys[ordinal % len(fmt_keys)]
        pairs.append((topic, fmt))

    # Ketma-ket kunlarda janr o'zgaradi
    assert all(pairs[i][1] != pairs[i + 1][1] for i in range(len(pairs) - 1))
    # 60 kunda barcha janrlar ishlatiladi
    assert {f for _, f in pairs} == set(fmt_keys)
    # Bir mavzu turli janrlarda chiqadi (25 va 6 o'zaro tub)
    by_topic: dict[str, set] = {}
    for topic, fmt in pairs:
        by_topic.setdefault(topic, set()).add(fmt)
    assert any(len(fmts) > 1 for fmts in by_topic.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,marker", [
    ("xato", "XATOLAR"),
    ("taqqoslash", "TAQQOSLASH"),
    ("mif", "MIFLARNI"),
    ("keys", "REAL HOLAT"),
    ("analogiya", "O'XSHATISH"),
    ("tarif", "TUSHUNTIRISH"),
])
async def test_educational_prompt_carries_requested_format(fmt, marker):
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_educational_post("RAG", "jonli", fmt)
    assert marker in captured["prompt"]
    assert "RAG" in captured["prompt"]


@pytest.mark.asyncio
async def test_educational_prompt_has_no_hardcoded_hashtags():
    """Hashtaglar promptga qotib yozilmagan — har post mavzusiga mos bo'ladi."""
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_educational_post("Embeddings", "jonli")
    p = captured["prompt"]
    assert "#SuniyIntellekt #AI #Texnologiya #Dars" not in p
    assert "MAVZUGA mos 3-4 ta hashtag" in p


@pytest.mark.asyncio
async def test_educational_unknown_format_falls_back_to_rotation():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_educational_post("LLM", "jonli", "yoq-bunday-janr")
    # Bugungi rotatsiya janri ishlatiladi (prompt janrsiz qolmaydi)
    today_fmt = news_fetcher.get_todays_educational_format()
    assert EDUCATIONAL_FORMATS[today_fmt].split("\n")[0] in captured["prompt"]


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
         patch.object(channel_poster, "_recent_topics", AsyncMock(return_value=[])), \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()) as send2:
        await channel_poster.create_on_demand("tool", "expert", "")
    # topic bo'sh — kunlik rotatsiyadan olinadi
    assert gt.await_args.args[0] in AI_TOOLS
    assert send2.await_args.args[1] == "tool"


# ─── mavzu takrorlanmasligi ────────────────────────────────────────────────────
# Mavzu faqat `ordinal % len(list)` bilan tanlanardi. Kanal haftada 5 post
# chiqargani uchun katalog bir necha haftada tugab, mavzular tarixga
# qaramasdan qayta boshlanardi — o'quvchiga "bir xil postlar" shu ko'rinardi.

def test_pick_fresh_skips_recently_posted():
    items = ["a", "b", "c", "d"]
    picked = news_fetcher._pick_fresh(items, recent=["a", "b", "c"])
    assert picked == "d"


def test_pick_fresh_without_history_keeps_date_rotation():
    from datetime import date
    items = ["a", "b", "c", "d"]
    assert news_fetcher._pick_fresh(items, None) == items[date.today().toordinal() % 4]


def test_pick_fresh_falls_back_to_least_recent():
    """Hamma mavzu chiqib bo'lgan — eng uzoq vaqt oldin chiqqani olinadi."""
    items = ["a", "b", "c"]
    # recent eng yangisi birinchi: c → b → a, demak eng eskisi "a"
    assert news_fetcher._pick_fresh(items, recent=["c", "b", "a"]) == "a"


@pytest.mark.parametrize("kind,items", [
    ("practical", PRACTICAL_TOPICS),
    ("tool", AI_TOOLS),
    ("educational", EDUCATIONAL_TOPICS),
])
def test_full_catalogue_runs_out_before_any_repeat(kind, items):
    """Tarix bilan tanlaganda ro'yxat to'liq tugamaguncha mavzu takrorlanmaydi."""
    history: list[str] = []
    for _ in range(len(items)):
        picked = news_fetcher._pick_fresh(items, history[:len(items)])
        assert picked not in history, f"{kind}: '{picked}' katalog tugamasdan takrorlandi"
        history.insert(0, picked)
    assert set(history) == set(items)


def test_practical_topics_are_not_all_text_writing():
    """Mavzular bir gala bo'lsa, turli mavzular ham bir xil o'qiladi."""
    assert len(PRACTICAL_TOPICS) >= 20
    writing = [t for t in PRACTICAL_TOPICS
               if any(w in t.lower() for w in ("yozish", "yozishma", "matn"))]
    assert len(writing) < len(PRACTICAL_TOPICS) / 2


# ─── amaliy postlarning shakli ─────────────────────────────────────────────────

def test_practical_format_rotation_within_list():
    assert news_fetcher.get_todays_practical_format() in PRACTICAL_FORMATS


def test_practical_format_rotates_daily_and_covers_all():
    keys = list(PRACTICAL_FORMATS)
    seq = [keys[o % len(keys)] for o in range(30)]
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))
    assert set(seq) == set(keys)


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,marker", [
    ("qadam", "QADAMMA-QADAM"),
    ("prompt", "TAYYOR PROMPT"),
    ("oldin_keyin", "YOMON PROMPT"),
    ("xato", "XATOLAR USTIDA"),
    ("checklist", "TEKSHIRUV RO'YXATI"),
])
async def test_practical_prompt_carries_requested_format(fmt, marker):
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_practical_post("CV yozish", "jonli", fmt)
    assert marker in captured["prompt"]
    assert "CV yozish" in captured["prompt"]


@pytest.mark.asyncio
async def test_practical_unknown_format_falls_back_to_rotation():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "post"

    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)):
        await news_fetcher.generate_practical_post("LLM", "jonli", "yoq-bunday-janr")
    today_fmt = news_fetcher.get_todays_practical_format()
    assert PRACTICAL_FORMATS[today_fmt].split("\n")[0] in captured["prompt"]


# ─── taqvim posti tarixni o'qiydi ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_demand_practical_avoids_recently_posted_topics():
    """Mavzu berilmasa — kanal tarixida yaqinda chiqqani qayta tanlanmaydi."""
    already = PRACTICAL_TOPICS[:5]
    with patch.object(channel_poster, "_recent_topics",
                      AsyncMock(return_value=already)) as hist, \
         patch("app.services.news_fetcher.news_fetcher.generate_practical_post",
               AsyncMock(return_value="post")) as gen, \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()):
        await channel_poster.create_on_demand("practical", "jonli", "")
    hist.assert_awaited_with("practical")
    assert gen.await_args.args[0] in PRACTICAL_TOPICS
    assert gen.await_args.args[0] not in already


@pytest.mark.asyncio
async def test_regenerate_avoids_rejected_topic_and_history():
    """«Boshqa post tayyorla» — rad etilgan mavzu ham tarixga qo'shiladi."""
    import json as _json
    rejected = PRACTICAL_TOPICS[7]
    history = [t for t in PRACTICAL_TOPICS[:5] if t != rejected]

    class FakeRedis:
        async def get(self, key):
            return _json.dumps({
                "text": "eski", "post_type": "practical",
                "topic": rejected, "style": "jonli", "category": "",
            })

        async def delete(self, key):
            return 1

    with patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())), \
         patch.object(channel_poster, "_recent_topics",
                      AsyncMock(return_value=history)), \
         patch("app.services.news_fetcher.news_fetcher.generate_practical_post",
               AsyncMock(return_value="yangi post")) as gen, \
         patch.object(channel_poster, "_send_for_approval", AsyncMock()):
        await channel_poster.regenerate_new_and_send_for_approval("abc123")

    picked = gen.await_args.args[0]
    assert picked != rejected
    assert picked not in history


@pytest.mark.asyncio
async def test_recent_topics_returns_empty_when_db_unavailable():
    """DB ga borib bo'lmasa post tarixsiz, lekin baribir chiqadi."""
    with patch("app.database.session.AsyncSessionLocal", side_effect=RuntimeError("db yo'q")):
        assert await channel_poster._recent_topics("practical") == []

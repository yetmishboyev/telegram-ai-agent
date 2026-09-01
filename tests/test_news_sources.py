"""Kengaytirilgan yangilik manbalari (RSS + Telegram, freshness) testlari."""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import (
    news_fetcher, NEWS_TG_CHANNELS, AI_NEWS_FEEDS, FEED_CONCURRENCY,
)


def test_is_fresh_recent_and_old():
    now = datetime.now(timezone.utc)
    assert news_fetcher._is_fresh(format_datetime(now - timedelta(hours=5))) is True
    assert news_fetcher._is_fresh(format_datetime(now - timedelta(hours=100))) is False
    # Sanasi yo'q/o'qilmaydigan item TASHLANADI — barcha feedlar pubDate beradi,
    # sana yo'qligi feed buzilganini bildiradi va eskirgan yangilik qayta-qayta
    # tanlanishiga olib kelardi.
    assert news_fetcher._is_fresh("g'alati sana") is False
    assert news_fetcher._is_fresh("") is False


@pytest.mark.asyncio
async def test_fetch_rss_filters_stale_and_sets_source():
    now = datetime.now(timezone.utc)
    xml = f"""<rss><channel>
      <item><title>Yangi xabar</title><link>http://x/a</link>
        <description>d</description><pubDate>{format_datetime(now)}</pubDate></item>
      <item><title>Eski xabar</title><link>http://x/b</link>
        <description>d</description><pubDate>{format_datetime(now - timedelta(days=10))}</pubDate></item>
    </channel></rss>"""

    class FakeResp:
        text = xml
        def raise_for_status(self): pass

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    with patch("httpx.AsyncClient", lambda *a, **k: FakeClient()):
        items = await news_fetcher.fetch_rss("https://www.wired.com/feed/tag/ai/latest/rss")

    titles = [i["title"] for i in items]
    assert "Yangi xabar" in titles
    assert "Eski xabar" not in titles
    assert items[0]["source"] == "wired.com"


@pytest.mark.asyncio
async def test_fetch_telegram_news_builds_items():
    now = datetime.now(timezone.utc)

    def m(mid, text, age_h):
        return SimpleNamespace(id=mid, text=text, date=now - timedelta(hours=age_h))

    long_text = "**Katta yangilik sarlavhasi**\n" + "Tafsilot matni. " * 20
    msgs = [
        m(1, long_text, 2),        # yangi, uzun — kiradi
        m(2, "qisqa", 1),          # qisqa — kirmaydi
        m(3, long_text, 90),       # eski — kirmaydi
    ]
    with patch("app.services.telegram_service.telegram_service._client") as client:
        client.get_messages = AsyncMock(return_value=msgs)
        items = await news_fetcher.fetch_telegram_news()

    # Har kanal uchun bitta mos xabar (mock hammaga bir xil qaytaradi)
    assert len(items) == len(NEWS_TG_CHANNELS)
    it = items[0]
    assert it["title"].startswith("Katta yangilik")
    assert it["source"] in NEWS_TG_CHANNELS
    assert it["link"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_get_ai_news_merges_rss_and_telegram():
    rss_item = {"title": "RSS yangilik", "link": "http://x", "desc": "", "source": "wired.com"}
    tg_item = {"title": "TG yangilik", "link": "https://t.me/c/1", "desc": "", "source": "@ai_newz"}
    with patch.object(news_fetcher, "fetch_rss", AsyncMock(return_value=[rss_item])), \
         patch.object(news_fetcher, "fetch_telegram_news", AsyncMock(return_value=[tg_item])):
        items = await news_fetcher.get_ai_news(count=10)
    titles = [i["title"] for i in items]
    assert "RSS yangilik" in titles
    assert "TG yangilik" in titles


@pytest.mark.asyncio
async def test_get_ai_news_survives_telegram_failure():
    rss_item = {"title": "RSS yangilik", "link": "http://x", "desc": "", "source": "s"}
    with patch.object(news_fetcher, "fetch_rss", AsyncMock(return_value=[rss_item])), \
         patch.object(news_fetcher, "fetch_telegram_news",
                      AsyncMock(side_effect=RuntimeError("client uzilgan"))):
        items = await news_fetcher.get_ai_news(count=5)
    assert [i["title"] for i in items] == ["RSS yangilik"] * 1


# ─── Atom feedlar ──────────────────────────────────────────────────────────────
# Ilgari faqat RSS (`<channel><item>`) o'qilardi: Atom manba ulangandek
# ko'rinib, aslida jimgina bo'sh ro'yxat qaytarardi.

def _fake_client(xml: str):
    class FakeResp:
        text = xml
        def raise_for_status(self): pass

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()
    return lambda *a, **k: FakeClient()


def test_parse_feed_date_handles_rss_and_atom():
    now = datetime.now(timezone.utc)
    assert news_fetcher._parse_feed_date(format_datetime(now)) is not None
    iso = news_fetcher._parse_feed_date("2026-09-01T09:00:00Z")
    assert iso is not None and iso.tzinfo is not None
    assert news_fetcher._parse_feed_date("g'alati") is None
    assert news_fetcher._parse_feed_date("") is None


def test_is_fresh_accepts_atom_iso_dates():
    now = datetime.now(timezone.utc)
    assert news_fetcher._is_fresh(now.isoformat().replace("+00:00", "Z")) is True
    old = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    assert news_fetcher._is_fresh(old) is False


@pytest.mark.asyncio
async def test_fetch_rss_reads_atom_feeds():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat().replace("+00:00", "Z")
    xml = f"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom yangilik</title>
        <link href="https://www.theverge.com/a"/>
        <summary>&lt;p&gt;matn&lt;/p&gt;</summary><published>{now}</published></entry>
      <entry><title>Eski atom</title>
        <link href="https://www.theverge.com/b"/>
        <summary>matn</summary><published>{old}</published></entry>
    </feed>"""

    with patch("httpx.AsyncClient", _fake_client(xml)):
        items = await news_fetcher.fetch_rss(
            "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
        )

    titles = [i["title"] for i in items]
    assert titles == ["Atom yangilik"]          # eskisi filtrlandi
    assert items[0]["link"] == "https://www.theverge.com/a"
    assert items[0]["desc"] == "matn"            # HTML teglari tozalandi
    assert items[0]["source"] == "theverge.com"


@pytest.mark.asyncio
async def test_fetch_rss_unknown_format_returns_empty_not_crash():
    with patch("httpx.AsyncClient", _fake_client("<nimadir><x/></nimadir>")):
        assert await news_fetcher.fetch_rss("https://x.com/feed") == []


# ─── manbalar ro'yxati ─────────────────────────────────────────────────────────

def test_feed_list_has_no_duplicates_and_is_https():
    assert len(AI_NEWS_FEEDS) == len(set(AI_NEWS_FEEDS))
    assert len(NEWS_TG_CHANNELS) == len(set(NEWS_TG_CHANNELS))
    assert all(ch.startswith("@") for ch in NEWS_TG_CHANNELS)


def test_feed_list_covers_primary_lab_sources():
    """Laboratoriyalarning o'z e'lonlari — ikkinchi qo'ldan emas, birlamchi manba."""
    joined = " ".join(AI_NEWS_FEEDS)
    for host in ("openai.com", "deepmind.google", "blog.google", "huggingface.co"):
        assert host in joined


# ─── parallel yuklash ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ai_news_fetches_feeds_concurrently():
    """Feedlar birin-ketin emas, parallel yuklanadi (sekin sayt bloklamasin)."""
    import asyncio
    active = 0
    peak = 0

    async def slow_fetch(url, limit=6):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return [{"title": url, "link": url, "desc": "", "source": url}]

    with patch.object(news_fetcher, "fetch_rss", AsyncMock(side_effect=slow_fetch)), \
         patch.object(news_fetcher, "fetch_telegram_news", AsyncMock(return_value=[])):
        await news_fetcher.get_ai_news(count=5)

    assert peak > 1, "feedlar hali ham birin-ketin yuklanmoqda"
    assert peak <= FEED_CONCURRENCY


@pytest.mark.asyncio
async def test_one_broken_feed_does_not_sink_the_rest():
    async def flaky(url, limit=6):
        if "venturebeat" in url:
            raise RuntimeError("feed yiqildi")
        return [{"title": f"t-{url}", "link": url, "desc": "", "source": url}]

    with patch.object(news_fetcher, "fetch_rss", AsyncMock(side_effect=flaky)), \
         patch.object(news_fetcher, "fetch_telegram_news", AsyncMock(return_value=[])):
        items = await news_fetcher.get_ai_news(count=5)

    assert items, "bitta feed yiqilganda hammasi yo'qoldi"
    assert all("venturebeat" not in i["source"] for i in items)


# ─── manbalar diagnostikasi ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_sources_reports_dead_and_silent_sources():
    async def fetch(url, limit=50):
        if "venturebeat" in url:
            raise RuntimeError("HTTP 404")
        if "openai" in url:
            return []                       # ulanadi, lekin jim
        return [{"title": "x", "link": "l", "desc": "", "source": "s"}]

    class FakeTG:
        _client = SimpleNamespace(
            get_messages=AsyncMock(side_effect=lambda ch, limit=5: (
                [] if ch == "@denissexy" else [object()]
            ))
        )

    with patch.object(news_fetcher, "fetch_rss", AsyncMock(side_effect=fetch)), \
         patch("app.services.telegram_service.telegram_service", FakeTG):
        report = await news_fetcher.check_sources()

    by_name = {r["name"]: r for r in report["feeds"]}
    assert by_name["venturebeat.com"]["ok"] is False
    assert by_name["openai.com"] == {
        "name": "openai.com", "ok": True, "fresh": 0,
        "note": "48 soatda yangilik yo'q",
    }
    assert by_name["techcrunch.com"]["fresh"] == 1
    assert len(report["channels"]) == len(NEWS_TG_CHANNELS)
    assert all(c["ok"] for c in report["channels"])

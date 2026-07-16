"""Kengaytirilgan yangilik manbalari (RSS + Telegram, freshness) testlari."""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher, NEWS_TG_CHANNELS


def test_is_fresh_recent_and_old():
    now = datetime.now(timezone.utc)
    assert news_fetcher._is_fresh(format_datetime(now - timedelta(hours=5))) is True
    assert news_fetcher._is_fresh(format_datetime(now - timedelta(hours=100))) is False
    # parse bo'lmaydigan sana — item tashlanmaydi
    assert news_fetcher._is_fresh("g'alati sana") is True
    assert news_fetcher._is_fresh("") is True


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

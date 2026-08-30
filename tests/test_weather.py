"""Ob-havo posti.

Asosiy shart: RAQAMLAR API DAN KELADI, LLM ulardan birortasini ham
yozmaydi. Ob-havo posti uchun eng yomon nosozlik — ishonch bilan e'lon
qilingan noto'g'ri harorat.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import weather
from app.services.channel_poster import channel_poster


def _rows():
    return [
        {"city": "Toshkent",  "min": 21, "max": 37, "rain": 0,  "code": 0},
        {"city": "Nukus",     "min": -3, "max": 5,  "rain": 60, "code": 71},
        {"city": "Samarqand", "min": 18, "max": 34, "rain": 10, "code": 2},
    ]


# ─── kod → tavsif ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected_emoji", [
    (0, "☀️"), (3, "☁️"), (61, "🌦"), (75, "❄️"), (95, "⛈"),
])
def test_known_codes_map_to_emoji(code, expected_emoji):
    assert weather.describe_code(code)[0] == expected_emoji


def test_unknown_code_falls_back():
    emoji, text = weather.describe_code(12345)
    assert emoji and text, "noma'lum kod ham bo'sh qoldirmasligi kerak"
    assert weather.describe_code(None)[0]


# ─── post formati ──────────────────────────────────────────────────────────────

def test_post_contains_every_city_and_temperature():
    post = weather.format_post(_rows())
    for row in _rows():
        assert row["city"] in post
    assert "+21…+37°" in post
    assert "-3…+5°" in post, "manfiy harorat to'g'ri belgilanishi kerak"


def test_rain_shown_only_when_significant():
    post = weather.format_post(_rows())
    assert "💧60%" in post          # Nukus — ko'rsatiladi
    assert "💧10%" not in post      # Samarqand — past, ko'rsatilmaydi
    assert "💧0%" not in post


def test_comment_included_when_given():
    assert "suv iching" in weather.format_post(_rows(), "Issiq kunda ko'proq suv iching")


def test_post_survives_without_comment():
    post = weather.format_post(_rows(), "")
    assert "💬" not in post
    assert "#obhavo" in post


# ─── ma'lumot olish ────────────────────────────────────────────────────────────

def _api_payload(n):
    return [
        {"daily": {"temperature_2m_min": [20.4], "temperature_2m_max": [35.6],
                   "precipitation_probability_max": [0], "weather_code": [0]}}
        for _ in range(n)
    ]


@pytest.mark.asyncio
async def test_fetch_parses_and_rounds():
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return _api_payload(len(weather.CITIES))

    with patch("httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.get = AsyncMock(return_value=R())
        rows = await weather.fetch()

    assert rows is not None and len(rows) == len(weather.CITIES)
    assert rows[0]["min"] == 20 and rows[0]["max"] == 36   # yaxlitlangan
    assert rows[0]["city"] == "Toshkent"


@pytest.mark.asyncio
async def test_fetch_returns_none_on_api_error():
    with patch("httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=RuntimeError("tarmoq"))
        assert await weather.fetch() is None


@pytest.mark.asyncio
async def test_fetch_rejects_incomplete_response():
    """Shaharlar soni mos kelmasa — noto'g'ri shaharga harorat yozib qo'ymaymiz."""
    class R:
        def raise_for_status(self): pass
        def json(self): return _api_payload(3)   # 13 emas

    with patch("httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.get = AsyncMock(return_value=R())
        assert await weather.fetch() is None


# ─── LLM raqam yozmasligi ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_comment_with_digits_is_discarded():
    """Model qoidani buzib harorat yozsa, izoh tashlanadi — post raqamsiz chiqadi."""
    with patch("app.services.news_fetcher.news_fetcher._call_llm",
               AsyncMock(return_value="Bugun 42 gradus, suv iching")):
        comment = await channel_poster._weather_comment(_rows())
    assert comment == ""


@pytest.mark.asyncio
async def test_clean_comment_is_kept():
    with patch("app.services.news_fetcher.news_fetcher._call_llm",
               AsyncMock(return_value='  "Issiqda ko\'proq suv iching"  ')):
        comment = await channel_poster._weather_comment(_rows())
    assert comment == "Issiqda ko'proq suv iching"


@pytest.mark.asyncio
async def test_comment_failure_does_not_break_the_post():
    with patch("app.services.news_fetcher.news_fetcher._call_llm",
               AsyncMock(side_effect=RuntimeError("LLM yiqildi"))):
        assert await channel_poster._weather_comment(_rows()) == ""


# ─── post yuborish ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_post_when_data_unavailable():
    """Ma'lumot yo'q bo'lsa kanalga HECH NARSA yuborilmaydi."""
    with patch("app.services.weather.fetch", AsyncMock(return_value=None)), \
         patch.object(channel_poster, "_send_to_channel",
                      AsyncMock(side_effect=AssertionError("yuborilmasligi kerak"))):
        await channel_poster.post_weather()


@pytest.mark.asyncio
async def test_post_is_sent_and_recorded():
    with patch("app.services.weather.fetch", AsyncMock(return_value=_rows())), \
         patch.object(channel_poster, "_weather_comment", AsyncMock(return_value="izoh")), \
         patch.object(channel_poster, "_send_to_channel", AsyncMock(return_value=555)) as send, \
         patch.object(channel_poster, "_save_channel_post", AsyncMock()) as save:
        await channel_poster.post_weather()

    assert "Toshkent" in send.await_args.args[0]
    assert save.await_args.kwargs["post_type"] == "weather"
    assert save.await_args.kwargs["telegram_message_id"] == 555


@pytest.mark.asyncio
async def test_nothing_recorded_when_send_fails():
    with patch("app.services.weather.fetch", AsyncMock(return_value=_rows())), \
         patch.object(channel_poster, "_weather_comment", AsyncMock(return_value="")), \
         patch.object(channel_poster, "_send_to_channel", AsyncMock(return_value=None)), \
         patch.object(channel_poster, "_save_channel_post",
                      AsyncMock(side_effect=AssertionError("saqlanmasligi kerak"))):
        await channel_poster.post_weather()


# ─── jadval ────────────────────────────────────────────────────────────────────

def test_scheduler_registers_weather_at_seven():
    import inspect
    from app.services import scheduler
    source = inspect.getsource(scheduler.SchedulerService.start)
    assert "channel_weather_morning" in source
    assert "post_weather" in source

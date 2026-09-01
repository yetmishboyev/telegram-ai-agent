"""Post muqova kartasi: matndan ajratish, chizish va postga biriktirish."""
import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.services import post_image
from app.services.channel_poster import channel_poster

POST = """🧭 **Prompt yozishda eng ko'p uchraydigan uchta xato**

Ko'pchilik modeldan noaniq javob olganda aybni modelda deb biladi.

**1. Kontekstsiz buyruq.** Model bo'shliqni o'zi to'ldiradi.

> Model sizning niyatingizni emas, matningizni o'qiydi.

#PromptEngineering #SuniyIntellekt
"""


# ─── sarlavha va sitatani ajratish ─────────────────────────────────────────────

def test_extract_takes_bold_heading_and_first_quote():
    title, subtitle = post_image.extract(POST)
    assert title == "Prompt yozishda eng ko'p uchraydigan uchta xato"
    assert subtitle == "Model sizning niyatingizni emas, matningizni o'qiydi."


def test_extract_strips_emoji_and_markdown():
    """Kartaga emoji chizilmaydi — DejaVu uni to'rtburchak qilib ko'rsatardi."""
    title, _ = post_image.extract("🎓 **_RAG_ nima?**\n\nMatn")
    assert "🎓" not in title and "*" not in title and "_" not in title
    assert title == "RAG nima?"


def test_extract_without_bold_falls_back_to_first_line():
    title, subtitle = post_image.extract("Oddiy sarlavha\n\nDavomi")
    assert title == "Oddiy sarlavha"
    assert subtitle == ""


def test_extract_skips_quote_that_repeats_the_title():
    """Sitata sarlavhani takrorlasa kartada bir gap ikki marta chiqardi."""
    _, subtitle = post_image.extract("**Bitta fikr**\n\n> Bitta fikr\n")
    assert subtitle == ""


def test_extract_accepts_escaped_quote_markers():
    """Model `>` ni `&gt;` yoki `\\>` qilib qaytarishi mumkin."""
    for marker in (">", "&gt;", "\\>"):
        _, subtitle = post_image.extract(f"**Sarlavha**\n\n{marker} Asosiy fikr\n")
        assert subtitle == "Asosiy fikr", marker


def test_extract_handles_empty_text():
    assert post_image.extract("") == ("", "")


# ─── chizish ───────────────────────────────────────────────────────────────────

def test_render_produces_png_for_every_post_type():
    for kind in post_image.STYLES:
        png = post_image.render(kind, "Sinov sarlavhasi", "Asosiy fikr", "1-sentabr")
        assert png and png[:8] == b"\x89PNG\r\n\x1a\n", kind


def test_render_without_title_returns_none():
    """Sarlavhasiz karta ma'nosiz — post matn bo'lib chiqsin."""
    assert post_image.render("educational", "  ") is None


def test_render_unknown_type_uses_fallback_not_crash():
    assert post_image.render("yoq-bunday-tur", "Sarlavha") is not None


def test_render_truncates_a_very_long_title():
    long_title = "juda uzun sarlavha " * 40
    png = post_image.render_for_post(f"**{long_title}**", "news")
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_for_post_reads_title_from_post_text():
    assert post_image.render_for_post(POST, "educational") is not None


def test_tint_lightens_towards_white():
    assert post_image._tint((100, 0, 0), 0.5) == (178, 128, 128)


# ─── postga biriktirish ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_card_saves_base64_and_returns_public_url():
    saved = {}

    class FakeRedis:
        async def setex(self, key, ttl, value):
            saved.update(key=key, ttl=ttl, value=value)

    with patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())):
        url = await channel_poster._store_card(POST, "educational")

    assert url and url.endswith(".png") and "/p/" in url
    card_id = url.rsplit("/p/", 1)[1].removesuffix(".png")
    assert saved["key"] == f"post_card:{card_id}"
    # Redis clienti decode_responses=True — xom baytlar buzilardi
    assert base64.b64decode(saved["value"])[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_store_card_returns_none_when_redis_is_down():
    """Karta saqlanmasa post rasmsiz, lekin baribir chiqadi."""
    with patch("app.database.redis.get_redis", AsyncMock(side_effect=RuntimeError("yo'q"))):
        assert await channel_poster._store_card(POST, "educational") is None


@pytest.mark.asyncio
async def test_send_to_channel_without_type_stays_plain_text():
    """Ob-havo kabi oqimlar kartasiz ishlashda davom etadi."""
    client = AsyncMock()
    client.send_message.return_value = type("M", (), {"id": 7})()
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})), \
         patch.object(channel_poster, "_store_card", AsyncMock()) as card:
        assert await channel_poster._send_to_channel("matn") == 7
    card.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_channel_falls_back_to_text_when_card_fails():
    client = AsyncMock()
    client.send_message.return_value = type("M", (), {"id": 11})()
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})), \
         patch.object(channel_poster, "_store_card", AsyncMock(return_value=None)):
        assert await channel_poster._send_to_channel("matn", "educational") == 11
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_send_to_channel_uses_card_when_available():
    client = AsyncMock()
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})), \
         patch.object(channel_poster, "_store_card",
                      AsyncMock(return_value="https://x/p/abc.png")), \
         patch.object(channel_poster, "_send_with_card", AsyncMock(return_value=99)) as send:
        assert await channel_poster._send_to_channel("matn", "news") == 99
    assert send.await_args.args[1] == "https://x/p/abc.png"
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_with_card_falls_back_to_plain_preview_on_rpc_error():
    """Katta muqova o'tmasa oddiy havola ko'rinishiga qaytadi, post yo'qolmaydi."""
    client = AsyncMock()
    client.side_effect = RuntimeError("RPC xatosi")
    client.send_message.return_value = type("M", (), {"id": 5})()
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})):
        assert await channel_poster._send_with_card("matn", "https://x/p/a.png") == 5
    sent = client.send_message.await_args.args[1]
    assert "https://x/p/a.png" in sent and sent.endswith("matn")


def test_message_id_from_reads_update_message_id():
    update = type("UpdateMessageID", (), {"id": 42})()
    result = type("Updates", (), {"updates": [update]})()
    assert channel_poster._message_id_from(result) == 42


def test_message_id_from_reads_new_channel_message():
    update = type("UpdateNewChannelMessage", (),
                  {"message": type("M", (), {"id": 77})()})()
    result = type("Updates", (), {"updates": [update]})()
    assert channel_poster._message_id_from(result) == 77


# ─── ochiq karta yo'li ─────────────────────────────────────────────────────────
# Telegram rasmni sessiyasiz yuklab oladi, shuning uchun yo'l parolsiz.
# Demak kalit shakli qat'iy tekshirilishi shart.

@pytest.mark.asyncio
async def test_card_route_returns_png():
    from app.api.routes.cards import get_post_card
    png = post_image.render("news", "Sarlavha")

    class FakeRedis:
        async def get(self, key):
            assert key == "post_card:abcdef01"
            return base64.b64encode(png).decode()

    with patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())):
        resp = await get_post_card("abcdef01")

    assert resp.media_type == "image/png"
    assert resp.body[:8] == b"\x89PNG\r\n\x1a\n"
    assert "max-age" in resp.headers["cache-control"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "post_card:*", "ABCDEF01", "abc", "a" * 40, "abc def",
])
async def test_card_route_rejects_malformed_ids(bad):
    """Kalit Redis so'roviga qo'shiladi — shakli noto'g'ri bo'lsa umuman bormaydi."""
    from fastapi import HTTPException
    from app.api.routes.cards import get_post_card
    redis = AsyncMock()
    with patch("app.database.redis.get_redis", AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as err:
            await get_post_card(bad)
    assert err.value.status_code == 404
    redis.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_card_route_404_when_expired():
    from fastapi import HTTPException
    from app.api.routes.cards import get_post_card

    class FakeRedis:
        async def get(self, key):
            return None

    with patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())):
        with pytest.raises(HTTPException) as err:
            await get_post_card("deadbeef")
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_send_with_card_prewarms_the_preview_before_sending():
    """Telegram karta manzilini oldin yuklab olishi shart.

    `InputMediaWebPage` allaqachon o'qilgan sahifani talab qiladi; karta
    manzili har postda yangi bo'lgani uchun busiz `WEBPAGE_NOT_FOUND`
    qaytardi va rasm matn ostiga tushib qolardi.
    """
    from telethon.tl.functions.messages import (
        GetWebPagePreviewRequest, SendMediaRequest,
    )
    calls = []

    async def rpc(request):
        calls.append(type(request))
        if isinstance(request, SendMediaRequest):
            update = type("UpdateMessageID", (), {"id": 31})()
            return type("Updates", (), {"updates": [update]})()
        return object()

    client = AsyncMock(side_effect=rpc)
    client._parse_message_text.return_value = ("matn", [])
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})):
        assert await channel_poster._send_with_card("matn", "https://x/p/a.png") == 31

    assert calls.index(GetWebPagePreviewRequest) < calls.index(SendMediaRequest), \
        "ko'rib chiqish yuborishdan OLDIN so'ralishi kerak"


@pytest.mark.asyncio
async def test_send_with_card_survives_a_failing_prewarm():
    """Oldindan yuklatish yiqilsa ham yuborish davom etsin."""
    from telethon.tl.functions.messages import (
        GetWebPagePreviewRequest, SendMediaRequest,
    )

    async def rpc(request):
        if isinstance(request, GetWebPagePreviewRequest):
            raise RuntimeError("preview xatosi")
        update = type("UpdateMessageID", (), {"id": 33})()
        return type("Updates", (), {"updates": [update]})()

    client = AsyncMock(side_effect=rpc)
    client._parse_message_text.return_value = ("matn", [])
    with patch("app.services.bot_service.bot_service", type("B", (), {"_client": client})):
        assert await channel_poster._send_with_card("matn", "https://x/p/a.png") == 33

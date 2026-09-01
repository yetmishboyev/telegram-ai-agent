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


# ─── postga biriktirish: rasm + izoh ───────────────────────────────────────────
# Post endi rasm FAYLI bilan bitta xabarda ketadi. Ilgari karta serverdan
# tarqatilib havola ko'rinishi orqali biriktirilardi — u ikki marta
# `WEBPAGE_NOT_FOUND` bilan yiqildi, chunki Telegram yangi manzilni asinxron
# o'qiydi va biriktirish poygada yutqazardi.

def _fake_bot(client):
    return patch("app.services.bot_service.bot_service", type("B", (), {"_client": client}))


def test_caption_len_ignores_tags_and_decodes_entities():
    """Telegram cheklovi HTML teglarga emas, ochilgan MATNGA tegishli."""
    from app.services.channel_poster import _caption_len
    assert _caption_len("<b>Salom</b>") == 5
    assert _caption_len('<a href="https://juda-uzun-manzil.com/x">uz</a>') == 2
    assert _caption_len("5 &gt; 3") == 5
    assert _caption_len("") == 0


@pytest.mark.asyncio
async def test_send_to_channel_attaches_the_card_as_a_photo():
    client = AsyncMock()
    client.send_file.return_value = type("M", (), {"id": 77})()
    with _fake_bot(client):
        assert await channel_poster._send_to_channel(POST, "educational") == 77

    client.send_message.assert_not_awaited()          # matnli yo'l ishlatilmadi
    kwargs = client.send_file.await_args.kwargs
    assert kwargs["caption"] == POST                  # post matni izoh bo'ldi
    assert kwargs["parse_mode"] == "html"


@pytest.mark.asyncio
async def test_send_to_channel_drops_the_image_when_the_caption_is_too_long():
    """Postni kesgandan ko'ra rasmsiz chiqargan afzal."""
    from app.services.channel_poster import CAPTION_LIMIT
    long_post = "**Sarlavha**\n\n" + "juda uzun matn " * 120
    assert len(long_post) > CAPTION_LIMIT

    client = AsyncMock()
    client.send_message.return_value = type("M", (), {"id": 8})()
    with _fake_bot(client):
        assert await channel_poster._send_to_channel(long_post, "news") == 8

    client.send_file.assert_not_awaited()
    assert client.send_message.await_args.args[1] == long_post   # matn to'liq


@pytest.mark.asyncio
async def test_send_to_channel_without_type_stays_plain_text():
    """Ob-havo kabi oqimlar o'z rasmini o'zi yuboradi — bu yo'l tegmasin."""
    client = AsyncMock()
    client.send_message.return_value = type("M", (), {"id": 7})()
    with _fake_bot(client):
        assert await channel_poster._send_to_channel("matn") == 7
    client.send_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_channel_falls_back_to_text_when_the_card_cannot_be_drawn():
    client = AsyncMock()
    client.send_message.return_value = type("M", (), {"id": 11})()
    with _fake_bot(client), \
         patch("app.services.post_image.render_for_post", return_value=None):
        assert await channel_poster._send_to_channel("matn", "educational") == 11
    client.send_file.assert_not_awaited()
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_send_to_channel_falls_back_to_text_when_the_photo_send_fails():
    client = AsyncMock()
    client.send_file.side_effect = RuntimeError("Telegram rad etdi")
    client.send_message.return_value = type("M", (), {"id": 12})()
    with _fake_bot(client):
        assert await channel_poster._send_to_channel(POST, "news") == 12
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_approved_post_reaches_the_channel_with_its_type():
    """Tasdiqlash oqimi post turini uzatadi — busiz rasm chizilmaydi."""
    import json
    from app.services.bot_service import bot_service

    class FakeRedis:
        async def get(self, key):
            return json.dumps({"text": POST, "post_type": "practical", "topic": "t"})
        async def delete(self, key):
            return 1

    event = AsyncMock()
    with patch("app.database.redis.get_redis", AsyncMock(return_value=FakeRedis())), \
         patch.object(channel_poster, "_send_to_channel",
                      AsyncMock(return_value=55)) as send, \
         patch.object(channel_poster, "_save_channel_post", AsyncMock()):
        await bot_service._approve_post(event, "abc123")

    assert send.await_args.args == (POST, "practical")

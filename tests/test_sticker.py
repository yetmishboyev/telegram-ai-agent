"""Stiker aniqlash — stiker hujjat (DOCUMENT) sifatida qaralmasligi kerak.

Telegram stikerni `MessageMediaDocument` ichida yuboradi, shuning uchun eski
`"Sticker" in media_class` sharti hech qachon bajarilmasdi va har bir stiker
"Hujjatingiz qabul qilindi" javobini olardi.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

from app.database.models import MessageType
from app.services.telegram_service import telegram_service


class MessageMediaDocument:
    """Telethon media klassining o'rnini bosuvchi — klass NOMI muhim."""


class MessageMediaPhoto:
    pass


def _message(media, attributes=None, text=None):
    return SimpleNamespace(
        media=media,
        document=SimpleNamespace(attributes=attributes or []) if attributes is not None else None,
        text=text,
    )


def _sticker_attr():
    return DocumentAttributeSticker(alt="😀", stickerset=None)


# ─── tur aniqlash ──────────────────────────────────────────────────────────────

def test_static_sticker_is_not_a_document():
    msg = _message(MessageMediaDocument(), [_sticker_attr()])
    assert telegram_service._detect_message_type(msg) == MessageType.STICKER


def test_video_sticker_is_still_a_sticker():
    """.webm stikerda DocumentAttributeVideo ham bo'ladi — VIDEO deb qaralmasin."""
    msg = _message(
        MessageMediaDocument(),
        [_sticker_attr(), DocumentAttributeVideo(duration=3, w=512, h=512)],
    )
    assert telegram_service._detect_message_type(msg) == MessageType.STICKER


def test_real_document_still_detected():
    msg = _message(MessageMediaDocument(), [DocumentAttributeFilename("CV_Aliyev.pdf")])
    assert telegram_service._detect_message_type(msg) == MessageType.DOCUMENT


def test_voice_still_detected():
    msg = _message(MessageMediaDocument(), [DocumentAttributeAudio(duration=5, voice=True)])
    assert telegram_service._detect_message_type(msg) == MessageType.VOICE


def test_video_still_detected():
    msg = _message(MessageMediaDocument(), [DocumentAttributeVideo(duration=10, w=640, h=480)])
    assert telegram_service._detect_message_type(msg) == MessageType.VIDEO


def test_photo_still_detected():
    assert telegram_service._detect_message_type(
        _message(MessageMediaPhoto(), None)
    ) == MessageType.IMAGE


def test_plain_text_still_detected():
    assert telegram_service._detect_message_type(
        SimpleNamespace(media=None, text="salom")
    ) == MessageType.TEXT


# ─── quvurdagi xulq ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sticker_only_batch_is_ignored():
    """Faqat stiker kelsa — na LLM chaqiriladi, na javob yuboriladi."""
    event = SimpleNamespace(
        message=_message(MessageMediaDocument(), [_sticker_attr()]),
        chat_id=555,
    )
    sender = SimpleNamespace(id=777, username="user", first_name="Ali", last_name=None)

    with patch(
        "app.services.telegram_service.ai_service.process_message",
        AsyncMock(side_effect=AssertionError("stiker uchun quvur chaqirilmasligi kerak")),
    ):
        await telegram_service._handle_batch([(event, sender)])

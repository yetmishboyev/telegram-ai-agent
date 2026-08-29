"""Ovoz transkripsiyasi (Faza 01).

Asosiy shart: transkripsiya HECH QACHON quvurni buzmasligi kerak. Kalit
yo'q, API yiqilgan, ovoz juda uzun, natija bo'sh — bularning hammasida
xabar eski yo'l bilan (media yorlig'i bilan) o'tishi kerak, istisno emas.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telethon.tl.types import DocumentAttributeAudio

from app.ai.transcriber import Transcriber, transcriber
from app.database.models import MessageType
from app.services.telegram_service import telegram_service


class MessageMediaDocument:
    """Klass nomi muhim — _detect_message_type unga qaraydi."""


class _FakeSession:
    """_handle_batch `async with AsyncSessionLocal()` ishlatadi — MagicMock buni qo'llab-quvvatlamaydi."""
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    async def commit(self): pass
    async def rollback(self): pass


def _voice_message(duration=8, mime="audio/ogg"):
    return SimpleNamespace(
        media=MessageMediaDocument(),
        document=SimpleNamespace(
            mime_type=mime,
            attributes=[DocumentAttributeAudio(duration=duration, voice=True)],
        ),
        text=None,
        id=1,
        download_media=AsyncMock(return_value=b"OggS-fake-audio"),
    )


# ─── mavjudlik darvozasi ───────────────────────────────────────────────────────

def test_unavailable_without_api_key():
    t = Transcriber()
    with patch("app.ai.transcriber.settings") as s:
        s.voice_enabled = True
        s.voice_provider = "openai"
        s.openai_api_key = ""
        assert t.is_available() is False


def test_unavailable_when_switched_off():
    t = Transcriber()
    with patch("app.ai.transcriber.settings") as s:
        s.voice_enabled = False
        s.voice_provider = "openai"
        s.openai_api_key = "sk-test"
        assert t.is_available() is False


def test_available_when_configured():
    t = Transcriber()
    with patch("app.ai.transcriber.settings") as s:
        s.voice_enabled = True
        s.voice_provider = "openai"
        s.openai_api_key = "sk-test"
        assert t.is_available() is True


# ─── fayl nomi (OpenAI formatni undan aniqlaydi) ───────────────────────────────

@pytest.mark.parametrize("mime,expected", [
    ("audio/ogg", "voice.ogg"),
    ("audio/mpeg", "voice.mp3"),
    ("audio/x-m4a", "voice.m4a"),
    ("AUDIO/WAV", "voice.wav"),
    (None, "voice.ogg"),
    ("nomalum/tur", "voice.ogg"),
])
def test_filename_derived_from_mime(mime, expected):
    assert Transcriber.filename_for(mime) == expected


# ─── transkripsiya xulqi ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_text_and_sends_language():
    t = Transcriber()
    create = AsyncMock(return_value="Assalomu alaykum, ertaga uchrashamizmi?")
    with patch.object(t, "is_available", return_value=True), \
         patch.object(t, "_get_client") as client, \
         patch("app.ai.usage_log.record"):
        client.return_value.audio.transcriptions.create = create
        text = await t.transcribe(b"audio", mime="audio/ogg", duration_sec=5)

    assert text == "Assalomu alaykum, ertaga uchrashamizmi?"
    assert create.call_args.kwargs["language"] == "uz"
    assert create.call_args.kwargs["file"].name == "voice.ogg"


@pytest.mark.asyncio
async def test_long_audio_is_skipped_without_calling_api():
    t = Transcriber()
    create = AsyncMock(side_effect=AssertionError("uzun ovoz uchun API chaqirilmasin"))
    with patch.object(t, "is_available", return_value=True), \
         patch.object(t, "_get_client") as client:
        client.return_value.audio.transcriptions.create = create
        assert await t.transcribe(b"audio", duration_sec=10_000) is None


@pytest.mark.asyncio
async def test_api_failure_returns_none_not_exception():
    t = Transcriber()
    with patch.object(t, "is_available", return_value=True), \
         patch.object(t, "_get_client") as client, \
         patch("app.ai.usage_log.record"):
        client.return_value.audio.transcriptions.create = AsyncMock(
            side_effect=RuntimeError("API yiqildi")
        )
        assert await t.transcribe(b"audio") is None


@pytest.mark.asyncio
async def test_empty_transcript_returns_none():
    t = Transcriber()
    with patch.object(t, "is_available", return_value=True), \
         patch.object(t, "_get_client") as client, \
         patch("app.ai.usage_log.record"):
        client.return_value.audio.transcriptions.create = AsyncMock(return_value="   ")
        assert await t.transcribe(b"audio") is None


@pytest.mark.asyncio
async def test_object_response_shape_also_accepted():
    """response_format o'zgarsa SDK `.text` maydonli obyekt qaytaradi."""
    t = Transcriber()
    with patch.object(t, "is_available", return_value=True), \
         patch.object(t, "_get_client") as client, \
         patch("app.ai.usage_log.record"):
        client.return_value.audio.transcriptions.create = AsyncMock(
            return_value=SimpleNamespace(text="matn")
        )
        assert await t.transcribe(b"audio") == "matn"


@pytest.mark.asyncio
async def test_no_audio_bytes_returns_none():
    assert await transcriber.transcribe(b"") is None


# ─── quvurga ulanish ───────────────────────────────────────────────────────────

def test_voice_message_type_detected():
    assert telegram_service._detect_message_type(_voice_message()) == MessageType.VOICE


def test_duration_and_mime_read_from_message():
    msg = _voice_message(duration=42, mime="audio/ogg")
    assert telegram_service._audio_duration(msg) == 42
    assert telegram_service._audio_mime(msg) == "audio/ogg"


@pytest.mark.asyncio
async def test_transcript_reaches_the_normal_pipeline():
    """Transkript matn sifatida quvurga tushadi — filtr va klassifikatsiya ishlaydi."""
    event = SimpleNamespace(message=_voice_message(), chat_id=7)
    sender = SimpleNamespace(id=9, username="ali", first_name="Ali", last_name=None)
    captured = {}

    async def fake_process(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1), None

    with patch("app.ai.transcriber.transcriber.is_available", return_value=True), \
         patch("app.ai.transcriber.transcriber.transcribe",
               AsyncMock(return_value="Ertaga soat 10 da uchrashamiz")), \
         patch("app.services.telegram_service.AsyncSessionLocal", _FakeSession), \
         patch("app.services.telegram_service.ai_service.process_message",
               AsyncMock(side_effect=fake_process)):
        await telegram_service._handle_batch([(event, sender)])

    assert "[Ovoz matni: Ertaga soat 10 da uchrashamiz]" in captured["text"]
    assert captured["message_type"] == MessageType.VOICE


@pytest.mark.asyncio
async def test_failed_transcription_falls_back_to_label():
    """Transkripsiya ishlamasa xabar yo'qolmaydi — eski yorliq bilan o'tadi."""
    event = SimpleNamespace(message=_voice_message(), chat_id=7)
    sender = SimpleNamespace(id=9, username="ali", first_name="Ali", last_name=None)
    captured = {}

    async def fake_process(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1), None

    with patch("app.ai.transcriber.transcriber.is_available", return_value=False), \
         patch("app.services.telegram_service.AsyncSessionLocal", _FakeSession), \
         patch("app.services.telegram_service.ai_service.process_message",
               AsyncMock(side_effect=fake_process)):
        await telegram_service._handle_batch([(event, sender)])

    assert captured["text"] == "🎤 Ovozli xabar yuborildi"

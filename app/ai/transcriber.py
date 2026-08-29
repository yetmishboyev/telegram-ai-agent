"""Ovozli xabarlarni matnga aylantirish.

O'zbek Telegramida odamlar yozmaydi — gapiradi. Ilgari ovozli xabar
"🎤 Ovozli xabar yuborildi" degan yorliqqa aylanib, mazmunsiz javob olardi.

Claude audio qabul qilmaydi, shuning uchun transkripsiya alohida servis
orqali bajariladi (hozircha OpenAI). Natija rasm oqimidagi kabi matnga
aylanib, odatiy quvurga uzatiladi — ya'ni maxfiy ma'lumot filtri,
guardrails, klassifikatsiya va FAQ transkript ustida ham ishlaydi.

Bu modul HECH QACHON istisno ko'tarmaydi: transkripsiya ishlamasa `None`
qaytadi va xabar eski yo'l bilan (media yorlig'i bilan) qayta ishlanadi.
"""
import io
import time

from loguru import logger

from app.ai import usage_log
from app.config import settings

# Telegram ovozli xabarlari OGG/Opus formatida keladi
_DEFAULT_FILENAME = "voice.ogg"

_MIME_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "video/mp4": "mp4",
}


class Transcriber:
    def __init__(self) -> None:
        self._client = None
        self._warned = False

    # ─── mavjudlik ────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Transkripsiya yoqilgan va kaliti bormi."""
        if not settings.voice_enabled:
            return False
        if settings.voice_provider != "openai":
            return False
        if not settings.openai_api_key:
            self._warn_once("OPENAI_API_KEY bo'sh — ovozli xabarlar matnga aylantirilmaydi")
            return False
        return True

    def _warn_once(self, message: str) -> None:
        if not self._warned:
            self._warned = True
            logger.warning(f"Ovoz transkripsiyasi o'chirilgan: {message}")

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    @staticmethod
    def filename_for(mime: str | None) -> str:
        """OpenAI formatni fayl nomidan aniqlaydi, shuning uchun kengaytma muhim."""
        extension = _MIME_EXTENSIONS.get((mime or "").lower())
        return f"voice.{extension}" if extension else _DEFAULT_FILENAME

    # ─── transkripsiya ────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio: bytes,
        mime: str | None = None,
        duration_sec: float | None = None,
    ) -> str | None:
        """Ovozni matnga aylantiradi. Muvaffaqiyatsizlikda None (istisno emas)."""
        if not self.is_available() or not audio:
            return None

        limit = settings.voice_max_duration_seconds
        if duration_sec and duration_sec > limit:
            logger.info(
                f"Ovozli xabar juda uzun ({duration_sec:.0f}s > {limit}s) — "
                f"transkripsiya qilinmadi"
            )
            return None

        stream = io.BytesIO(audio)
        stream.name = self.filename_for(mime)

        started = time.perf_counter()
        try:
            response = await self._get_client().audio.transcriptions.create(
                model=settings.voice_model,
                file=stream,
                language=settings.voice_language,
                response_format="text",
            )
        except Exception as e:
            logger.error(f"Transkripsiyada xato: {e}")
            usage_log.record(
                agent="Transcriber", model=settings.voice_model, tier="audio",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(e).__name__}: {e}",
            )
            return None

        # response_format="text" da SDK xom satr qaytaradi; boshqa formatlarda
        # `.text` maydonli obyekt keladi — ikkovini ham qabul qilamiz.
        text = response if isinstance(response, str) else getattr(response, "text", "")
        text = (text or "").strip()

        usage_log.record(
            agent="Transcriber", model=settings.voice_model, tier="audio",
            latency_ms=int((time.perf_counter() - started) * 1000),
            extra={
                "duration_sec": round(duration_sec, 1) if duration_sec else None,
                "chars": len(text),
                "audio_bytes": len(audio),
            },
        )

        if not text:
            logger.info("Transkripsiya bo'sh natija qaytardi (jim yoki tanilmagan nutq)")
            return None

        logger.info(f"Ovoz matnga aylantirildi ({len(text)} belgi): {text[:80]}...")
        return text


transcriber = Transcriber()

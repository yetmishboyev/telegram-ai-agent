import asyncio
import random
from collections import deque
from pathlib import Path
from loguru import logger

from telethon import TelegramClient, events
from telethon.tl.types import (
    User as TelegramUser,
    PeerUser,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

from app.config import settings
from app.database.session import AsyncSessionLocal
from app.database.redis import get_redis
from app.database.models import MessageType
from app.services.ai_service import ai_service
from app.ai.memory.short_term import short_term_memory


SESSION_PATH = Path("sessions") / settings.telegram_session_name

OWNER_ACTIVE_TTL = 600    # 10 daqiqa — owner yozgandan keyin AI javob bermaydi
DEBOUNCE_SECONDS = 5      # Bir nechta xabar kelganda shu vaqt kutiladi

# Agent o'zi yuborgan javoblar chiquvchi xabar sifatida ham qaytib keladi.
# Ular "ega yozdi" deb hisoblansa ikki zarar bo'lardi: (1) agent o'z javobidan
# keyin o'sha chatda OWNER_ACTIVE_TTL davomida jim qolardi, (2) o'z matnini
# eganing uslub namunasi sifatida o'rganib, uslub asta-sekin o'ziga qarab
# siljib ketardi. Shuning uchun yuborilgan xabar id lari eslab qolinadi.
AGENT_SENT_MEMORY = 200        # eslab qolinadigan xabar id lari soni
AGENT_SEND_GUARD_SECONDS = 15  # id qaytib kelgunicha chat bo'yicha himoya oynasi


class TelegramService:
    """Telethon orqali Telegram UserBot boshqaruvi."""

    def __init__(self) -> None:
        SESSION_PATH.parent.mkdir(exist_ok=True)
        self._client = TelegramClient(
            str(SESSION_PATH),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        self._me: TelegramUser | None = None
        # chat_id → kutilayotgan eventlar ro'yxati
        self._pending: dict[int, list] = {}
        # chat_id → debounce task
        self._debounce_tasks: dict[int, asyncio.Task] = {}
        # Agent yuborgan xabarlar id si (eng eskisi avtomatik siqib chiqariladi)
        self._agent_sent_ids: deque[int] = deque(maxlen=AGENT_SENT_MEMORY)
        # Hozir yuborilayotgan chatlar — id hali ma'lum bo'lmagan oniy oraliq
        # uchun (update handler send_message qaytishidan oldin ishlashi mumkin)
        self._sending_chats: set[int] = set()

    async def start(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("Telegram sessiyasi topilmadi. Avval autentifikatsiya qiling.")
        self._me = await self._client.get_me()
        logger.info(f"Telegram ulandi: {self._me.first_name} (@{self._me.username})")
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def on_new_message(event: events.NewMessage.Event) -> None:
            await self._enqueue(event)

        @self._client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
        async def on_outgoing_message(event: events.NewMessage.Event) -> None:
            # Agentning O'Z javobi — ega yozgani hisoblanmaydi
            if self._is_agent_message(event):
                logger.debug(f"Chiquvchi xabar agentniki ({event.chat_id}) — o'tkazib yuborildi")
                return
            await self._mark_owner_active(event.chat_id)
            if event.message.text:
                asyncio.create_task(self._learn_style(event.message.text))

    # ─── Debounce: bir nechta xabarni birga yig'ish ───────────────────────────

    async def _enqueue(self, event: events.NewMessage.Event) -> None:
        """Xabarni navbatga qo'shadi, DEBOUNCE_SECONDS kutib batch yuboradi."""
        sender = await event.get_sender()
        if not isinstance(sender, TelegramUser) or sender.bot:
            return
        if self._me and sender.id == self._me.id:
            return
        if await self._is_owner_active(event.chat_id):
            logger.debug(f"Owner aktiv ({event.chat_id}) — AI javob bermaydi")
            return

        chat_id = event.chat_id
        if chat_id not in self._pending:
            self._pending[chat_id] = []
        self._pending[chat_id].append((event, sender))

        # Avvalgi debounce taskni bekor qil, yangisini boshlat
        old = self._debounce_tasks.get(chat_id)
        if old and not old.done():
            old.cancel()
        self._debounce_tasks[chat_id] = asyncio.create_task(
            self._debounce_flush(chat_id)
        )

    async def _debounce_flush(self, chat_id: int) -> None:
        """DEBOUNCE_SECONDS kutib, yig'ilgan xabarlarni birga qayta ishlaydi."""
        await asyncio.sleep(DEBOUNCE_SECONDS)
        batch = self._pending.pop(chat_id, [])
        self._debounce_tasks.pop(chat_id, None)
        if not batch:
            return
        await self._handle_batch(batch)

    # ─── Batch qayta ishlash ──────────────────────────────────────────────────

    async def _handle_batch(self, batch: list) -> None:
        """Bir yoki bir nechta xabarni birga qayta ishlaydi."""
        first_event, sender = batch[0]

        # Barcha xabarlardan matn + media tavsifini yig'amiz
        parts: list[str] = []
        last_event = first_event

        for event, _ in batch:
            msg_type = self._detect_message_type(event.message)
            text = event.message.text or ""  # media uchun bu izoh (caption)

            if msg_type == MessageType.IMAGE:
                # Rasmni matnli tavsifga aylantiramiz — keyin odatiy matnli quvur
                # (klassifikatsiya, FAQ, eskalatsiya, javob) uni qayta ishlaydi.
                description = await self._describe_image(event.message)
                if description:
                    img_part = f"[Rasm tavsifi: {description}]"
                    text = f"{text}\n{img_part}" if text else img_part
                elif not text:
                    text = self._media_label(msg_type)
            elif msg_type == MessageType.VOICE:
                # Ovozni matnga aylantirib odatiy quvurga uzatamiz — shunda
                # maxfiy filtr, guardrails, klassifikatsiya va FAQ transkript
                # ustida ham ishlaydi. Transkripsiya bo'lmasa eski yorliq.
                transcript = await self._transcribe_voice(event.message)
                if transcript:
                    voice_part = f"[Ovoz matni: {transcript}]"
                    text = f"{text}\n{voice_part}" if text else voice_part
                elif not text:
                    text = self._media_label(msg_type)
            elif msg_type == MessageType.DOCUMENT:
                # Fayl nomi egaga yuboriladigan bildirishnomada ko'rinsin
                # ("CV_Aliyev.pdf" — "📎 Fayl yuborildi" dan foydaliroq).
                label = self._document_label(event.message)
                text = f"{text}\n{label}" if text else label
            elif msg_type == MessageType.STICKER:
                # Stiker javob talab qilmaydi — unga matn bilan javob berish
                # g'alati ko'rinadi. Yorliq ham qo'shilmaydi, shunda faqat
                # stiker kelgan batch umuman qayta ishlanmaydi.
                text = ""
            elif not text and event.message.media:
                text = self._media_label(msg_type)

            if text:
                parts.append(text)
            last_event = event

        if not parts:
            return

        combined_text = "\n".join(parts)

        # Agar bir nechta xabar bo'lsa — logga yoz
        if len(batch) > 1:
            logger.info(
                f"Batch keldi ({len(batch)} xabar): "
                f"{sender.first_name} (@{sender.username}) → {combined_text[:80]}..."
            )
        else:
            logger.info(
                f"Xabar keldi: {sender.first_name} (@{sender.username}) "
                f"→ {combined_text[:60]}..."
            )

        # Bitta xabar sifatida qayta ishlash — birinchi xabar_id ga reply qilinadi
        message_type = self._detect_message_type(last_event.message)
        if len(batch) > 1:
            message_type = MessageType.TEXT  # batch bo'lsa text sifatida saqlaymiz

        async with AsyncSessionLocal() as db:
            try:
                # Blacklist tekshiruvi ai_service.process_message ichida (bitta
                # joyda) amalga oshiriladi — bu yerda alohida so'rov kerak emas.
                msg, agent_response = await ai_service.process_message(
                    db=db,
                    telegram_id=sender.id,
                    text=combined_text,
                    username=sender.username,
                    first_name=sender.first_name,
                    last_name=sender.last_name,
                    message_type=message_type,
                    telegram_message_id=first_event.message.id,
                )
                await db.commit()

                if agent_response:
                    await self._send_response(first_event, msg, agent_response, db)
                    await db.commit()

            except Exception as e:
                await db.rollback()
                logger.error(f"Batch qayta ishlashda xato: {e}", exc_info=True)

    @staticmethod
    def _document_label(message) -> str:
        """Hujjat uchun fayl nomi bilan yorliq (nomi topilmasa umumiy yorliq)."""
        from telethon.tl.types import DocumentAttributeFilename
        doc = getattr(message, "document", None)
        if doc is not None:
            for attr in getattr(doc, "attributes", []) or []:
                if isinstance(attr, DocumentAttributeFilename) and attr.file_name:
                    return f"📎 Hujjat yuborildi: {attr.file_name}"
        return "📎 Hujjat yuborildi"

    @staticmethod
    def _media_label(msg_type: MessageType) -> str:
        labels = {
            MessageType.IMAGE:    "📷 Rasm yuborildi",
            MessageType.VIDEO:    "🎥 Video yuborildi",
            MessageType.VOICE:    "🎤 Ovozli xabar yuborildi",
            MessageType.DOCUMENT: "📎 Fayl yuborildi",
            MessageType.STICKER:  "🎭 Sticker yuborildi",
            MessageType.OTHER:    "📁 Fayl yuborildi",
        }
        return labels.get(msg_type, "📁 Fayl yuborildi")

    # ─── Yordamchi metodlar ───────────────────────────────────────────────────

    async def _describe_image(self, message) -> str | None:
        """Rasmni yuklab, vision agenti orqali matnli tavsifga aylantiradi."""
        try:
            image_bytes = await message.download_media(file=bytes)
            if not image_bytes:
                return None
            from app.ai.agents.vision_agent import vision_agent
            return await vision_agent.describe(
                image_bytes, media_type=self._image_mime(message)
            )
        except Exception as e:
            logger.error(f"Rasmni tavsiflashda xato: {e}")
            return None

    async def _transcribe_voice(self, message) -> str | None:
        """Ovozli xabarni yuklab, matnga aylantiradi (xato bo'lsa None).

        Fayl serverga saqlanmaydi — baytlar xotirada qoladi va transkripsiyadan
        keyin yo'qoladi. Bu hujjatlar bo'yicha qabul qilingan qaror bilan bir xil.
        """
        from app.ai.transcriber import transcriber
        if not transcriber.is_available():
            return None

        # Uzunlik yuklab olishdan OLDIN tekshiriladi — aks holda 30 daqiqalik
        # ovoz to'liq xotiraga tortilib, keyin tashlanardi.
        duration = self._audio_duration(message)
        if duration and duration > settings.voice_max_duration_seconds:
            logger.info(
                f"Ovozli xabar juda uzun ({duration:.0f}s) — yuklab olinmadi"
            )
            return None

        try:
            audio = await message.download_media(file=bytes)
            if not audio:
                return None
            return await transcriber.transcribe(
                audio,
                mime=self._audio_mime(message),
                duration_sec=duration,
            )
        except Exception as e:
            logger.error(f"Ovozni matnga aylantirishda xato: {e}")
            return None

    @staticmethod
    def _audio_mime(message) -> str | None:
        doc = getattr(message, "document", None)
        return getattr(doc, "mime_type", None) if doc is not None else None

    @staticmethod
    def _audio_duration(message) -> float | None:
        """Ovoz uzunligi (soniya) — uzun xabarlarni kesish uchun."""
        doc = getattr(message, "document", None)
        for attr in (getattr(doc, "attributes", None) or []):
            if isinstance(attr, DocumentAttributeAudio):
                return getattr(attr, "duration", None)
        return None

    @staticmethod
    def _image_mime(message) -> str:
        doc = getattr(message, "document", None)
        if doc is not None and getattr(doc, "mime_type", None) in (
            "image/jpeg", "image/png", "image/gif", "image/webp"
        ):
            return doc.mime_type
        return "image/jpeg"

    async def _learn_style(self, text: str) -> None:
        try:
            from app.ai.agents.style_learner import style_learner
            await style_learner.learn(text)
        except Exception as e:
            logger.debug(f"Style learning xatosi: {e}")

    # ─── agentning o'z xabarlarini ajratish ───────────────────────────────────

    def _is_agent_message(self, event) -> bool:
        """Chiquvchi xabarni agentning o'zi yuborganmi."""
        return (
            event.chat_id in self._sending_chats
            or getattr(event.message, "id", None) in self._agent_sent_ids
        )

    async def _send_as_agent(self, chat_id: int, text: str, reply_to: int | None = None):
        """Agent nomidan xabar yuboradi va uni "ega yozdi" deb belgilanishdan saqlaydi.

        Chat id himoya to'plamiga yuborishdan OLDIN qo'shiladi: Telethon
        chiquvchi update'ni `send_message` qaytishidan oldin ham yetkazishi
        mumkin, bunda xabar id si hali ma'lum bo'lmaydi.

        Id yozilishi bilanoq oyna DARHOL yopiladi. Ilgari u 15 soniya ochiq
        turardi va shu vaqt ichida EGA o'zi yozgan xabar ham "agentniki" deb
        hisoblanardi — ya'ni ega qo'lda javob bersa `owner_active` qo'yilmay,
        agent keyingi xabarga baribir avtojavob berardi. Id va oyna orasida
        `await` yo'q, shuning uchun poyga xavfi ham yo'q.
        """
        self._sending_chats.add(chat_id)
        recorded = False
        try:
            message = await self._client.send_message(chat_id, text, reply_to=reply_to)
            message_id = getattr(message, "id", None)
            if message_id is not None:
                self._agent_sent_ids.append(message_id)
                recorded = True
            return message
        finally:
            if recorded:
                self._sending_chats.discard(chat_id)
            else:
                # Id olinmadi — tanish uchun boshqa belgi yo'q, shuning uchun
                # oyna qisqa muddat ochiq qoladi.
                try:
                    asyncio.get_running_loop().call_later(
                        AGENT_SEND_GUARD_SECONDS, self._sending_chats.discard, chat_id
                    )
                except RuntimeError:  # ishlayotgan loop yo'q (test muhiti)
                    self._sending_chats.discard(chat_id)

    async def _mark_owner_active(self, chat_id: int) -> None:
        r = await get_redis()
        await r.setex(f"owner_active:{chat_id}", OWNER_ACTIVE_TTL, "1")

    async def _is_owner_active(self, chat_id: int) -> bool:
        r = await get_redis()
        return bool(await r.get(f"owner_active:{chat_id}"))

    async def _send_response(self, event, db_message, response: str, db) -> None:
        """Javobni kechikish bilan yuboradi."""
        delay = random.uniform(
            settings.agent_min_delay_seconds,
            settings.agent_max_delay_seconds,
        )

        async with self._client.action(event.chat_id, "typing"):
            await asyncio.sleep(delay)

        if await self._is_owner_active(event.chat_id):
            logger.info(f"Owner delay paytida javob berdi — AI bekor qildi ({event.chat_id})")
            return

        try:
            await self._send_as_agent(
                event.chat_id,
                response,
                reply_to=event.message.id,
            )
            await ai_service.mark_sent(db, db_message)
            logger.info(f"Javob yuborildi: {response[:60]}...")
        except Exception as e:
            logger.error(f"Javob yuborishda xato: {e}")

    @staticmethod
    def _detect_message_type(message) -> MessageType:
        if not message.media:
            return MessageType.TEXT
        media = message.media
        media_class = type(media).__name__

        if "Photo" in media_class:
            return MessageType.IMAGE
        if "Document" in media_class and message.document:
            attributes = message.document.attributes or []
            # Stiker ham MessageMediaDocument bo'lib keladi — shuning uchun u
            # BIRINCHI tekshiriladi: aks holda oddiy stiker DOCUMENT bo'lib
            # "hujjat qabul qilindi" javobini olardi, video-stiker (.webm) esa
            # DocumentAttributeVideo tufayli VIDEO deb belgilanardi.
            if any(isinstance(attr, DocumentAttributeSticker) for attr in attributes):
                return MessageType.STICKER
            for attr in attributes:
                if isinstance(attr, DocumentAttributeAudio):
                    return MessageType.VOICE if attr.voice else MessageType.OTHER
                if isinstance(attr, DocumentAttributeVideo):
                    return MessageType.VIDEO
            return MessageType.DOCUMENT
        return MessageType.OTHER

    def is_ready(self) -> bool:
        """UserBot haqiqatan xabarga javob bera oladigan holatdami.

        Faqat TCP `is_connected()` yetarli emas — sessiya avtorizatsiyadan
        o'tmagan bo'lsa ham ulanish ochiq bo'lishi mumkin. `_me` esa faqat
        muvaffaqiyatli avtorizatsiyadan keyin o'rnatiladi, shuning uchun
        ikkovini birga tekshiramiz.
        """
        return self._me is not None and self._client.is_connected()

    async def send_message(self, chat_id: int, text: str, as_agent: bool = False) -> None:
        """Userbot orqali xabar yuboradi.

        `as_agent=True` — matnni agent generatsiya qilgan (masalan tizim
        ogohlantirishi): u eganing uslub namunasi sifatida o'rganilmasligi
        kerak. Relay uchun `False` qoladi — u yerdagi matnni ega yozadi,
        demak owner-active belgisi ham, uslub o'rganish ham o'rinli.
        """
        if as_agent:
            await self._send_as_agent(chat_id, text)
        else:
            await self._client.send_message(chat_id, text)

    async def run_until_disconnected(self) -> None:
        while True:
            try:
                await self._client.run_until_disconnected()
                logger.warning("Telegram ulanishi uzildi. Qayta ulanmoqda...")
            except Exception as e:
                logger.error(f"Telegram xatosi: {e}. 10 soniyadan keyin qayta ulanadi...")
            await asyncio.sleep(10)
            try:
                await self._client.connect()
                if await self._client.is_user_authorized():
                    logger.info("Telegram qayta ulandi")
                    # _register_handlers() qayta CHAQIRILMAYDI —
                    # Telethon handlerlarni disconnect/connect siklidayam saqlaydi,
                    # qayta chaqirsak duplikat handlerlar qo'shiladi.
                else:
                    logger.error("Telegram sessiyasi muddati tugagan")
                    self._me = None  # is_ready() endi to'g'ri "disconnected" qaytaradi
                    try:
                        from app.services.notification_service import notification_service
                        await notification_service.notify_error(
                            "Telegram UserBot",
                            "Sessiya muddati tugadi — qayta autentifikatsiya qilish kerak. "
                            "Agent endi shaxsiy xabarlarga javob bera olmaydi.",
                        )
                    except Exception as notify_err:
                        logger.error(f"Uzilish haqida bildirishnoma yuborilmadi: {notify_err}")
                    break
            except Exception as e:
                logger.error(f"Qayta ulanishda xato: {e}")

    async def disconnect(self) -> None:
        await self._client.disconnect()
        await short_term_memory.close()


telegram_service = TelegramService()

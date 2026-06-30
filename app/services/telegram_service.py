import asyncio
import random
from pathlib import Path
from loguru import logger

from telethon import TelegramClient, events
from telethon.tl.types import (
    User as TelegramUser,
    PeerUser,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
)

from app.config import settings
from app.database.session import AsyncSessionLocal
from app.database.models import MessageType
from app.services.ai_service import ai_service
from app.ai.memory.short_term import short_term_memory


SESSION_PATH = Path("sessions") / settings.telegram_session_name


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

    async def start(self) -> None:
        """Clientni ishga tushiradi va event handlerlarni ro'yxatdan o'tkazadi."""
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("Telegram sessiyasi topilmadi. Avval autentifikatsiya qiling.")
        self._me = await self._client.get_me()
        logger.info(f"Telegram ulandi: {self._me.first_name} (@{self._me.username})")
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def on_new_message(event: events.NewMessage.Event) -> None:
            await self._handle_incoming(event)

    async def _handle_incoming(self, event: events.NewMessage.Event) -> None:
        """Kelgan shaxsiy xabarni qayta ishlaydi."""
        sender = await event.get_sender()
        if not isinstance(sender, TelegramUser) or sender.bot:
            return

        # O'z xabarlarimizni e'tiborsiz qoldiramiz
        if self._me and sender.id == self._me.id:
            return

        message_type = self._detect_message_type(event.message)
        text = event.message.text or ""

        # Media xabarlar uchun tavsif
        if not text and event.message.media:
            text = f"[{message_type.value.upper()} yuborildi]"

        if not text:
            return

        logger.info(
            f"Xabar keldi: {sender.first_name} (@{sender.username}) → {text[:60]}..."
        )

        async with AsyncSessionLocal() as db:
            try:
                from sqlalchemy import select
                from app.database.models import TelegramUser
                result = await db.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == sender.id)
                )
                db_user = result.scalar_one_or_none()
                if db_user and db_user.is_blacklisted:
                    logger.info(f"Blacklistdagi foydalanuvchi: {sender.first_name} ({sender.id}) — e'tiborsiz qoldirildi")
                    return

                msg, agent_response = await ai_service.process_message(
                    db=db,
                    telegram_id=sender.id,
                    text=text,
                    username=sender.username,
                    first_name=sender.first_name,
                    last_name=sender.last_name,
                    message_type=message_type,
                    telegram_message_id=event.message.id,
                )
                await db.commit()

                if agent_response:
                    await self._send_response(event, msg, agent_response, db)
                    await db.commit()

            except Exception as e:
                await db.rollback()
                logger.error(f"Xabarni qayta ishlashda xato: {e}", exc_info=True)

    async def _send_response(self, event, db_message, response: str, db) -> None:
        """Javobni kechikish bilan yuboradi (tabiiy ko'rinish uchun)."""
        delay = random.uniform(
            settings.agent_min_delay_seconds,
            settings.agent_max_delay_seconds,
        )

        # Typing indikatori
        async with self._client.action(event.chat_id, "typing"):
            await asyncio.sleep(delay)

        try:
            await self._client.send_message(
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
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeAudio):
                    return MessageType.VOICE if attr.voice else MessageType.OTHER
                if isinstance(attr, DocumentAttributeVideo):
                    return MessageType.VIDEO
            return MessageType.DOCUMENT
        if "Sticker" in str(media_class):
            return MessageType.STICKER
        return MessageType.OTHER

    async def send_message(self, chat_id: int, text: str) -> None:
        """Admin dashboard uchun to'g'ridan-to'g'ri xabar yuborish."""
        await self._client.send_message(chat_id, text)

    async def run_until_disconnected(self) -> None:
        await self._client.run_until_disconnected()

    async def disconnect(self) -> None:
        await self._client.disconnect()
        await short_term_memory.close()


telegram_service = TelegramService()

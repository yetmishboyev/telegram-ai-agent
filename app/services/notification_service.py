from loguru import logger
from telethon import Button

from app.config import settings
from app.database.models import TelegramUser


class NotificationService:
    """Egaga muhim xabarlar haqida bildirishnoma yuboradi."""

    async def notify_important(
        self,
        user: TelegramUser,
        message_text: str,
        reason: str,
        category: str = "important",
    ) -> None:
        """Muhim xabarni egaga bot orqali yuboradi."""
        if not settings.owner_telegram_id:
            return

        from app.services.bot_service import bot_service
        if not bot_service._client:
            logger.warning("Bot client yo'q — bildirishnoma yuborilmadi")
            return

        name = user.display_name
        username_part = f" (@{user.username})" if user.username else ""
        preview = message_text[:400] + ("..." if len(message_text) > 400 else "")

        emoji_map = {
            "important": "🔔",
            "business":  "💼",
            "job":       "👔",
            "conference": "🎤",
            "media":     "📺",
        }
        emoji = emoji_map.get(category, "🔔")

        text = (
            f"{emoji} **Muhim xabar!**\n\n"
            f"👤 **{name}**{username_part}\n"
            f"🆔 `{user.telegram_id}`\n\n"
            f"📩 _{preview}_\n\n"
            f"🏷 **Sabab:** {reason}"
        )

        # "Javob berish" tugmasi — ega chatga o'tmasdan shu yerdan javob yozadi,
        # javob userbot orqali foydalanuvchiga yetkaziladi (ikki tomonlama relay).
        buttons = [[Button.inline("✍️ Javob berish", f"relay:{user.telegram_id}".encode())]]

        try:
            await bot_service._client.send_message(
                settings.owner_telegram_id,
                text,
                parse_mode="md",
                buttons=buttons,
            )
            logger.info(
                f"Bildirishnoma yuborildi: {name} ({user.telegram_id}) — {reason}"
            )
        except Exception as e:
            logger.error(f"Bildirishnoma yuborishda xato: {e}")

    async def notify_error(self, context: str, error: str) -> None:
        """Kritik xatolarni egaga yuboradi."""
        if not settings.owner_telegram_id:
            return
        from app.services.bot_service import bot_service
        if not bot_service._client:
            return
        try:
            await bot_service._client.send_message(
                settings.owner_telegram_id,
                f"⚠️ **Agent xatosi**\n\n`{context}`\n\n`{error[:300]}`",
                parse_mode="md",
            )
        except Exception:
            pass


notification_service = NotificationService()

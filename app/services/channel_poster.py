import json
import re
import uuid
from loguru import logger

from app.config import settings

CHANNEL = "@Yetmishboyev_Sh"
CHANNEL_FOOTER_HTML = (
    '\n\n—\n<a href="https://t.me/Yetmishboyev_Sh">📢 Kanalga obuna bo\'lishni unutmang</a>'
)


def _md_to_html(text: str) -> str:
    """Telegram Markdown → HTML (bold, italic)."""
    # **bold** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    # *italic* → <i>italic</i>  (bitta yulduzcha, lekin URL va emoji ichida emas)
    text = re.sub(r'(?<![/\w\*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    # _italic_ → <i>italic</i>  (URL ichidagi _ ga tegmaydi)
    text = re.sub(r'(?<![/\w])_(.+?)_(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    return text


class ChannelPoster:

    async def _send_to_channel(self, text: str) -> int | None:
        """To'g'ridan-to'g'ri kanalga yuboradi. Muvaffaqiyatli bo'lsa telegram_message_id qaytaradi."""
        try:
            from app.services.bot_service import bot_service
            if not bot_service._client:
                logger.warning("Bot client tayyor emas")
                return None
            msg = await bot_service._client.send_message(CHANNEL, text, parse_mode="html")
            logger.info(f"Kanal post yuborildi: {text[:60]}...")
            return msg.id
        except Exception as e:
            logger.error(f"Kanal post xatosi: {e}")
            return None

    async def _save_channel_post(
        self, telegram_message_id: int, post_type: str, topic: str, text: str
    ) -> None:
        """Kanalga yuborilgan postni DB ga saqlaydi."""
        try:
            from app.database.session import AsyncSessionLocal
            from app.database.models import ChannelPost
            import re as _re
            clean = _re.sub(r'<[^>]+>', '', text)
            async with AsyncSessionLocal() as db:
                post = ChannelPost(
                    telegram_message_id=telegram_message_id,
                    post_type=post_type,
                    topic=topic or "",
                    text_preview=clean[:500].strip(),
                    views=0,
                )
                db.add(post)
                await db.commit()
                logger.info(f"ChannelPost saqlandi: id={telegram_message_id}, type={post_type}")
        except Exception as e:
            logger.error(f"ChannelPost saqlashda xato: {e}")

    async def _send_for_approval(
        self, text: str, post_type: str, topic: str = ""
    ) -> None:
        """Postni egaga tasdiqlash uchun botda ko'rsatadi."""
        from telethon import Button
        from app.services.bot_service import bot_service
        from app.ai.memory.short_term import short_term_memory

        post_id = str(uuid.uuid4())[:8]
        text_with_footer = _md_to_html(text.rstrip()) + CHANNEL_FOOTER_HTML

        r = await short_term_memory._get_redis()
        await r.setex(
            f"pending_post:{post_id}",
            86400,
            json.dumps({"text": text_with_footer, "post_type": post_type, "topic": topic}),
        )

        labels = {
            "educational": "🎓 Ta'limiy post",
            "news": "🌐 Yangiliklar post",
            "digest": "📊 Haftalik dayjest",
        }
        label = labels.get(post_type, "📝 Post")

        await bot_service._client.send_message(
            settings.owner_telegram_id,
            f"📝 <b>{label} — tasdiqlash kerak</b>\n\n{text_with_footer}",
            parse_mode="html",
            buttons=[
                [
                    Button.inline("✅ Tasdiqlash",         f"approve_post:{post_id}".encode()),
                    Button.inline("✏️ Tahrirlash",          f"edit_post:{post_id}".encode()),
                ],
                [
                    Button.inline("🔄 Boshqa post tayyorla", f"regen_post:{post_id}".encode()),
                    Button.inline("❌ Rad etish",            f"reject_post:{post_id}".encode()),
                ],
            ],
        )
        logger.info(f"Post tasdiqlash uchun yuborildi (id={post_id})")

    async def regenerate_and_send_for_approval(
        self, post_id: str, feedback: str
    ) -> None:
        """Feedbackka asosan postni qayta tayyorlab, yana tasdiqlashga yuboradi."""
        from app.ai.memory.short_term import short_term_memory
        from app.services.news_fetcher import news_fetcher

        r = await short_term_memory._get_redis()
        raw = await r.get(f"pending_post:{post_id}")
        if not raw:
            logger.warning(f"Tahrirlash uchun post topilmadi: {post_id}")
            return
        data = json.loads(raw)
        await r.delete(f"pending_post:{post_id}")

        new_text = await news_fetcher.regenerate_post(
            original_text=data["text"],
            feedback=feedback,
            post_type=data["post_type"],
            topic=data.get("topic", ""),
        )
        await self._send_for_approval(new_text, data["post_type"], data.get("topic", ""))

    async def regenerate_new_and_send_for_approval(self, post_id: str) -> None:
        """Avvalgi postni o'chirib, tamomila boshqa mavzuda yangi post tayyorlab yuboradi."""
        from app.ai.memory.short_term import short_term_memory
        from app.services.news_fetcher import news_fetcher

        r = await short_term_memory._get_redis()
        raw = await r.get(f"pending_post:{post_id}")
        if not raw:
            logger.warning(f"Qayta yaratish uchun post topilmadi: {post_id}")
            return
        data = json.loads(raw)
        await r.delete(f"pending_post:{post_id}")

        post_type = data["post_type"]
        old_topic = data.get("topic", "")

        if post_type == "educational":
            # Eski mavzudan farqli yangi mavzu tanlaymiz
            new_topic = news_fetcher.get_different_topic(old_topic)
            logger.info(f"Boshqa ta'limiy post: '{old_topic}' → '{new_topic}'")
            new_text = await news_fetcher.generate_educational_post(new_topic)
            await self._send_for_approval(new_text, "educational", new_topic)
        else:
            # Yangiliklar uchun tasodifiy tartibda oladi — boshqa maqolalar chiqadi
            news_items = await news_fetcher.get_ai_news_shuffled(count=3)
            new_text = await news_fetcher.generate_news_post(news_items)
            await self._send_for_approval(new_text, "news")

    async def refresh_views(self) -> None:
        """Kanalga yuborilgan postlarning ko'rish sonini Telegram'dan yangilaydi."""
        try:
            from app.database.session import AsyncSessionLocal
            from app.database.models import ChannelPost
            from app.services.telegram_service import telegram_service
            from sqlalchemy import select
            from datetime import datetime, timezone

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChannelPost).where(
                        ChannelPost.telegram_message_id.isnot(None)
                    ).order_by(ChannelPost.sent_at.desc()).limit(30)
                )
                posts = result.scalars().all()

            if not posts or not telegram_service._client:
                return

            ids = [p.telegram_message_id for p in posts]
            messages = await telegram_service._client.get_messages(CHANNEL, ids=ids)

            updated = 0
            async with AsyncSessionLocal() as db:
                for msg in messages:
                    if not msg:
                        continue
                    for post in posts:
                        if post.telegram_message_id == msg.id:
                            post.views = msg.views or 0
                            post.views_updated_at = datetime.now(timezone.utc)
                            db.add(post)
                            updated += 1
                await db.commit()

            logger.info(f"Ko'rishlar yangilandi: {updated} ta post")
        except Exception as e:
            logger.error(f"Ko'rishlarni yangilashda xato: {e}")

    async def post_weekly_digest(self) -> None:
        """Yakshanba 12:00 — haftalik dayjest (egaga tasdiqlashga yuboriladi)."""
        try:
            from datetime import datetime, timedelta, timezone
            from sqlalchemy import select, desc
            from app.database.session import AsyncSessionLocal
            from app.database.models import ChannelPost
            from app.services.news_fetcher import news_fetcher

            week_ago = datetime.now(timezone.utc) - timedelta(days=7)

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChannelPost)
                    .where(
                        ChannelPost.sent_at >= week_ago,
                        ChannelPost.telegram_message_id.isnot(None),
                    )
                    .order_by(desc(ChannelPost.views))
                    .limit(6)
                )
                posts = result.scalars().all()

            if not posts:
                logger.warning("Haftalik dayjest: bu hafta post topilmadi")
                return

            posts_data = [
                {
                    "post_type": p.post_type,
                    "topic": p.topic,
                    "text_preview": p.text_preview,
                    "views": p.views,
                    "telegram_message_id": p.telegram_message_id,
                }
                for p in posts
            ]

            logger.info(f"Haftalik dayjest tayyorlanmoqda: {len(posts_data)} ta post")
            text = await news_fetcher.generate_weekly_digest(posts_data)
            await self._send_for_approval(text, "digest")
        except Exception as e:
            logger.error(f"Haftalik dayjest xatosi: {e}")

    async def post_educational(self) -> None:
        """09:00 — AI ta'limiy post (egaga tasdiqlashga yuboriladi)."""
        try:
            from app.services.news_fetcher import news_fetcher
            topic = news_fetcher.get_todays_topic()
            logger.info(f"Ta'limiy post tayyorlanmoqda: {topic}")
            text = await news_fetcher.generate_educational_post(topic)
            await self._send_for_approval(text, "educational", topic)
        except Exception as e:
            logger.error(f"Ta'limiy post xatosi: {e}")

    async def post_news(self) -> None:
        """12:00 va 16:00 — AI yangiliklari post (egaga tasdiqlashga yuboriladi)."""
        try:
            from app.services.news_fetcher import news_fetcher
            logger.info("AI yangiliklari yig'ilmoqda...")
            news_items = await news_fetcher.get_ai_news(count=3)
            text = await news_fetcher.generate_news_post(news_items)
            await self._send_for_approval(text, "news")
        except Exception as e:
            logger.error(f"Yangiliklar post xatosi: {e}")


channel_poster = ChannelPoster()

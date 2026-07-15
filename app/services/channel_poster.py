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
        self, text: str, post_type: str, topic: str = "", style: str = ""
    ) -> None:
        """Postni egaga tasdiqlash uchun botda ko'rsatadi."""
        from telethon import Button
        from app.services.bot_service import bot_service
        from app.database.redis import get_redis

        if not bot_service._client:
            logger.warning("Bot client tayyor emas — post egaga yuborib bo'lmadi")
            return

        post_id = str(uuid.uuid4())[:8]
        text_with_footer = _md_to_html(text.rstrip()) + CHANNEL_FOOTER_HTML

        r = await get_redis()
        await r.setex(
            f"pending_post:{post_id}",
            86400,
            json.dumps({
                "text": text_with_footer, "post_type": post_type,
                "topic": topic, "style": style,
            }),
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
        from app.database.redis import get_redis
        from app.services.news_fetcher import news_fetcher

        r = await get_redis()
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
        from app.database.redis import get_redis
        from app.services.news_fetcher import news_fetcher

        r = await get_redis()
        raw = await r.get(f"pending_post:{post_id}")
        if not raw:
            logger.warning(f"Qayta yaratish uchun post topilmadi: {post_id}")
            return
        data = json.loads(raw)
        await r.delete(f"pending_post:{post_id}")

        post_type = data["post_type"]
        old_topic = data.get("topic", "")
        style = data.get("style") or "chapani"

        if post_type == "educational":
            # Eski mavzudan farqli yangi mavzu tanlaymiz
            new_topic = news_fetcher.get_different_topic(old_topic)
            logger.info(f"Boshqa ta'limiy post: '{old_topic}' → '{new_topic}'")
            new_text = await news_fetcher.generate_educational_post(new_topic, style)
            await self._send_for_approval(new_text, "educational", new_topic, style)
        elif post_type == "free":
            new_text = await news_fetcher.generate_free_post(old_topic, style)
            await self._send_for_approval(new_text, "free", old_topic, style)
        else:
            # Yangiliklar: tasodifiy tartib — curation boshqa yangilikni tanlaydi
            new_text, topic = await self._build_deep_news(style, shuffled=True)
            if new_text:
                await self._send_for_approval(new_text, "news", topic, style)

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
            from app.services.news_fetcher import news_fetcher, DEFAULT_STYLE
            topic = news_fetcher.get_todays_topic()
            logger.info(f"Ta'limiy post tayyorlanmoqda: {topic}")
            text = await news_fetcher.generate_educational_post(topic, DEFAULT_STYLE)
            await self._send_for_approval(text, "educational", topic, DEFAULT_STYLE)
        except Exception as e:
            logger.error(f"Ta'limiy post xatosi: {e}")

    async def post_news(self) -> None:
        """12:00 va 16:00 — AI yangiligi: curation + BITTA yangilikka chuqur tahlil."""
        try:
            from app.services.news_fetcher import news_fetcher, DEFAULT_STYLE
            logger.info("AI yangiliklari yig'ilmoqda (curation uchun keng ro'yxat)...")
            text, topic = await self._build_deep_news(DEFAULT_STYLE)
            if not text:
                logger.warning("Yangilik topilmadi — post o'tkazib yuborildi")
                return
            await self._send_for_approval(text, "news", topic, DEFAULT_STYLE)
        except Exception as e:
            logger.error(f"Yangiliklar post xatosi: {e}")

    async def _build_deep_news(
        self, style: str, shuffled: bool = False
    ) -> tuple[str | None, str]:
        """Keng yangilik ro'yxatidan eng muhimini tanlab, chuqur post tayyorlaydi.

        Returns: (post_matni | None, tanlangan sarlavha).
        """
        from app.services.news_fetcher import news_fetcher
        if shuffled:
            items = await news_fetcher.get_ai_news_shuffled(count=10)
        else:
            items = await news_fetcher.get_ai_news(count=10)
        picked = await news_fetcher.curate_top_news(items)
        if not picked:
            return None, ""
        item = picked["item"]
        logger.info(f"Curation tanladi: {item['title'][:70]} — {picked['reason'][:80]}")
        text = await news_fetcher.generate_deep_news_post(
            item, style, curation_reason=picked["reason"]
        )
        return text, item["title"][:200]

    async def create_on_demand(self, post_type: str, style: str, topic: str = "") -> None:
        """Bot menyusidan chaqiriladigan on-demand post yaratish (tur + uslub)."""
        try:
            from app.services.news_fetcher import news_fetcher
            if post_type == "educational":
                topic = topic or news_fetcher.get_todays_topic()
                text = await news_fetcher.generate_educational_post(topic, style)
            elif post_type == "news":
                text, topic = await self._build_deep_news(style)
                if not text:
                    await self._notify_owner_text("❌ Hozircha yangilik topilmadi — keyinroq urinib ko'ring.")
                    return
            else:  # free — ega bergan mavzu
                text = await news_fetcher.generate_free_post(topic, style)
            await self._send_for_approval(text, post_type, topic, style)
        except Exception as e:
            logger.error(f"On-demand post xatosi: {e}")

    # ─── interaktiv quiz / so'rovnoma ───────────────────────────────────────────

    async def _send_poll_to_channel(self, quiz: dict) -> int | None:
        """Kanalga native poll/quiz yuboradi (Telegram Bot API sendPoll)."""
        import httpx
        is_quiz = quiz.get("kind") == "quiz"
        payload: dict = {
            "chat_id": CHANNEL,
            "question": quiz["question"],
            "options": [{"text": o[:100]} for o in quiz["options"]],
            "is_anonymous": True,
            "type": "quiz" if is_quiz else "regular",
        }
        if is_quiz:
            payload["correct_option_id"] = quiz.get("correct_index", 0)
            if quiz.get("explanation"):
                payload["explanation"] = quiz["explanation"]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPoll",
                    json=payload,
                )
                data = resp.json()
            if not data.get("ok"):
                logger.error(f"sendPoll xatosi: {data}")
                return None
            logger.info(f"Kanal poll yuborildi: {quiz['question'][:50]}...")
            return data["result"]["message_id"]
        except Exception as e:
            logger.error(f"Poll yuborishda xato: {e}")
            return None

    async def create_quiz_on_demand(self, kind: str, topic: str) -> None:
        """Bot menyusidan chaqiriladi: quiz/so'rovnoma generatsiya + tasdiqlash."""
        try:
            from app.services.news_fetcher import news_fetcher
            quiz = await news_fetcher.generate_quiz(topic, kind)
            if not quiz:
                await self._notify_owner_text("❌ Quiz tayyorlab bo'lmadi. Mavzuni aniqroq yozib qayta urinib ko'ring.")
                return
            await self._send_poll_for_approval(quiz, topic)
        except Exception as e:
            logger.error(f"Quiz on-demand xatosi: {e}")

    async def _send_poll_for_approval(self, quiz: dict, topic: str) -> None:
        from telethon import Button
        from app.services.bot_service import bot_service
        from app.database.redis import get_redis

        if not bot_service._client:
            return
        poll_id = str(uuid.uuid4())[:8]
        quiz["topic"] = topic
        r = await get_redis()
        await r.setex(f"pending_poll:{poll_id}", 86400, json.dumps(quiz))

        head = "❓ Quiz" if quiz["kind"] == "quiz" else "📊 So'rovnoma"
        lines = [f"{head} — tasdiqlash kerak\n", f"❔ {quiz['question']}\n"]
        for i, o in enumerate(quiz["options"]):
            mark = " ✅" if quiz["kind"] == "quiz" and i == quiz.get("correct_index") else ""
            lines.append(f"{i + 1}. {o}{mark}")
        if quiz.get("explanation"):
            lines.append(f"\n💡 {quiz['explanation']}")

        await bot_service._client.send_message(
            settings.owner_telegram_id,
            "\n".join(lines),
            buttons=[
                [Button.inline("✅ Nashr", f"approve_poll:{poll_id}".encode()),
                 Button.inline("🔄 Boshqa", f"regen_poll:{poll_id}".encode())],
                [Button.inline("❌ Rad etish", f"reject_poll:{poll_id}".encode())],
            ],
        )
        logger.info(f"Poll tasdiqlash uchun yuborildi (id={poll_id})")

    async def regenerate_poll(self, poll_id: str) -> None:
        from app.database.redis import get_redis
        r = await get_redis()
        raw = await r.get(f"pending_poll:{poll_id}")
        if not raw:
            return
        old = json.loads(raw)
        await r.delete(f"pending_poll:{poll_id}")
        await self.create_quiz_on_demand(old["kind"], old.get("topic", ""))

    # ─── obunachi statistikasi ──────────────────────────────────────────────────

    async def snapshot_subscribers(self) -> int | None:
        """Kanal obunachilari sonini Bot API'dan olib DB ga yozadi (har 6 soatda)."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getChatMemberCount",
                    params={"chat_id": CHANNEL},
                )
                data = resp.json()
            if not data.get("ok"):
                logger.error(f"getChatMemberCount xatosi: {data}")
                return None
            count = int(data["result"])
        except Exception as e:
            logger.error(f"Obunachi sonini olishda xato: {e}")
            return None

        try:
            from app.database.session import AsyncSessionLocal
            from app.database.models import SubscriberSnapshot
            async with AsyncSessionLocal() as db:
                db.add(SubscriberSnapshot(count=count))
                await db.commit()
            logger.info(f"Obunachi snapshot: {count}")
            return count
        except Exception as e:
            logger.error(f"Obunachi snapshot saqlashda xato: {e}")
            return None

    async def _notify_owner_text(self, text: str) -> None:
        try:
            from app.services.bot_service import bot_service
            if bot_service._client:
                await bot_service._client.send_message(settings.owner_telegram_id, text)
        except Exception as e:
            logger.error(f"Egaga xabar yuborishda xato: {e}")


channel_poster = ChannelPoster()

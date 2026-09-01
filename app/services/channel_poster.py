import json
import re
import uuid
from loguru import logger

from app.config import settings

CHANNEL = "@Yetmishboyev_Sh"
CHANNEL_FOOTER_HTML = (
    '\n\n—\n<a href="https://t.me/Yetmishboyev_Sh">📢 Kanalga obuna bo\'lishni unutmang</a>'
)


def _quotes_to_html(text: str) -> str:
    """`> ` bilan boshlangan qatorlarni Telegram sitata blokiga aylantiradi.

    Ketma-ket kelgan `>` qatorlar BITTA sitataga birlashtiriladi — aks holda
    Telegram har qatorni alohida blok qilib ko'rsatib, post titraydi.
    Model ba'zan `>` ni `&gt;` yoki `\\>` ko'rinishida qaytaradi, ikkovi ham
    qabul qilinadi. Qator o'rtasidagi ">" (masalan "5 > 3") tegilmaydi.
    """
    quote_re = re.compile(r'^\s*(?:&gt;|\\>|>)\s?(.*)$')
    result: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            result.append('<blockquote>' + '\n'.join(buffer) + '</blockquote>')
            buffer.clear()

    for line in text.split('\n'):
        match = quote_re.match(line)
        if match:
            buffer.append(match.group(1).strip())
        else:
            flush()
            result.append(line)
    flush()
    return '\n'.join(result)


def _md_to_html(text: str) -> str:
    """Telegram Markdown → HTML (bold, italic, sitata)."""
    # **bold** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    # *italic* → <i>italic</i>  (bitta yulduzcha, lekin URL va emoji ichida emas)
    text = re.sub(r'(?<![/\w\*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    # _italic_ → <i>italic</i>  (URL ichidagi _ ga tegmaydi)
    text = re.sub(r'(?<![/\w])_(.+?)_(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    # `> ` qatorlari → <blockquote> (qalin/kursiv ichida saqlanib qoladi)
    text = _quotes_to_html(text)
    return text


class ChannelPoster:

    async def _send_to_channel(self, text: str, post_type: str = "") -> int | None:
        """Kanalga post yuboradi. Muvaffaqiyatli bo'lsa message_id qaytaradi.

        `post_type` berilsa post uchun muqova kartasi chiziladi va rasm matn
        ustida ko'rinadi. Karta chizilmasa yoki yuborilmasa post oddiy matn
        bo'lib chiqadi — rasm hech qachon postni to'sib qo'ymasligi kerak.
        """
        try:
            from app.services.bot_service import bot_service
            if not bot_service._client:
                logger.warning("Bot client tayyor emas")
                return None

            if post_type:
                card_url = await self._store_card(text, post_type)
                if card_url:
                    msg_id = await self._send_with_card(text, card_url)
                    if msg_id:
                        return msg_id
                    logger.warning("Muqovali post o'tmadi — matnli postga qaytildi")

            msg = await bot_service._client.send_message(CHANNEL, text, parse_mode="html")
            logger.info(f"Kanal post yuborildi: {text[:60]}...")
            return msg.id
        except Exception as e:
            logger.error(f"Kanal post xatosi: {e}")
            return None

    async def _store_card(self, text: str, post_type: str) -> str | None:
        """Post uchun muqova kartasini chizib saqlaydi va uning manzilini qaytaradi.

        Karta rasm sifatida EMAS, havola ko'rinishi (link preview) orqali
        yuboriladi. Sababi o'lchov: Telegram rasm izohi 1024 belgi, postlar esa
        1200-1900 belgi — ya'ni "rasm + izoh" postni kesib tashlagan bo'lardi.
        Havola ko'rinishida matn to'liq qoladi va rasm uning ustida turadi.

        Karta chizilmasa yoki Redis ishlamasa `None` qaytadi — post baribir
        matn ko'rinishida chiqadi.
        """
        import base64
        import uuid as _uuid
        try:
            from app.services import post_image
            from app.services import weather
            from app.database.redis import get_redis
            from app.api.routes.cards import CARD_KEY, CARD_TTL

            png = post_image.render_for_post(text, post_type, weather.uzbek_date())
            if not png:
                return None

            card_id = _uuid.uuid4().hex[:16]
            r = await get_redis()
            await r.setex(
                CARD_KEY.format(card_id), CARD_TTL,
                base64.b64encode(png).decode("ascii"),
            )
            url = f"{settings.public_base_url.rstrip('/')}/p/{card_id}.png"
            logger.info(f"Muqova kartasi tayyor ({len(png) // 1024} KB): {url}")
            return url
        except Exception as e:
            logger.warning(f"Muqova kartasi tayyorlanmadi — post rasmsiz chiqadi: {e}")
            return None

    async def _send_with_card(self, text: str, card_url: str) -> int | None:
        """Postni muqova kartasi MATN USTIDA turadigan holda yuboradi.

        `invert_media` — havola ko'rinishini matndan yuqoriga chiqaradi,
        `force_large_media` esa uni kichik belgi emas, katta rasm qilib
        ko'rsatadi. Telethon'ning yuqori darajali `send_message` metodi bu
        ikkovini ochib bermaydi, shuning uchun so'rov qo'lda yig'iladi.

        MUHIM — avval ko'rib chiqish so'raladi: `InputMediaWebPage` Telegram
        ALLAQACHON o'qigan sahifani talab qiladi. Karta manzili har postda
        yangi bo'lgani uchun birinchi urinish `WEBPAGE_NOT_FOUND` bilan
        yiqilardi va post rasm matn OSTIDA turgan holda chiqardi.
        `GetWebPagePreview` Telegramni manzilni o'sha zahoti yuklab olishga
        majbur qiladi, shundan keyingina u media sifatida biriktiriladi.

        Xato bo'lsa oddiy havola ko'rinishiga, u ham bo'lmasa toza matnga
        qaytamiz — rasm hech qachon postni to'sib qo'ymasligi kerak.
        """
        from app.services.bot_service import bot_service
        client = bot_service._client

        # Ko'rinmas havola: matnda belgi ko'rinmaydi, lekin Telegram uni o'qiydi
        marked = f'<a href="{card_url}">\u200b</a>{text}'
        try:
            from telethon import helpers
            from telethon.tl.functions.messages import (
                GetWebPagePreviewRequest, SendMediaRequest,
            )
            from telethon.tl.types import InputMediaWebPage

            # Telegram kartani yuklab olsin — busiz keyingi qadam yiqiladi
            try:
                await client(GetWebPagePreviewRequest(message=card_url))
            except Exception as e:
                logger.debug(f"Ko'rib chiqish oldindan so'ralmadi: {e}")

            entity = await client.get_input_entity(CHANNEL)
            message, entities = await client._parse_message_text(marked, "html")
            result = await client(SendMediaRequest(
                peer=entity,
                media=InputMediaWebPage(url=card_url, force_large_media=True),
                message=message,
                entities=entities,
                invert_media=True,          # rasm matn USTIDA
                random_id=helpers.generate_random_long(),
            ))
            msg_id = self._message_id_from(result)
            if msg_id:
                logger.info("Kanal posti muqova kartasi bilan yuborildi")
                return msg_id
            logger.warning("Karta bilan yuborildi, lekin message_id topilmadi")
        except Exception as e:
            logger.warning(f"Katta muqova bilan yuborilmadi ({e}) — oddiy ko'rinishga o'tildi")

        try:
            msg = await client.send_message(
                CHANNEL, marked, parse_mode="html", link_preview=True,
            )
            return msg.id
        except Exception as e:
            logger.error(f"Muqovali post yuborilmadi: {e}")
            return None

    @staticmethod
    def _message_id_from(result) -> int | None:
        """RPC natijasidan yangi xabar id sini oladi.

        `SendMediaRequest` `Updates` qaytaradi va yangi xabar id si turli
        update turlarida keladi (kanal uchun odatda `UpdateMessageID` yoki
        `UpdateNewChannelMessage`), shuning uchun ikkovi ham qaraladi.
        """
        for update in getattr(result, "updates", []) or []:
            msg_id = getattr(update, "id", None)
            if msg_id and type(update).__name__ == "UpdateMessageID":
                return msg_id
            message = getattr(update, "message", None)
            if message is not None and getattr(message, "id", None):
                return message.id
        return getattr(result, "id", None)

    async def _send_photo_to_channel(self, image: bytes, caption: str) -> int | None:
        """Kanalga rasm + izoh yuboradi. Muvaffaqiyatli bo'lsa message_id qaytaradi."""
        import io
        try:
            from app.services.bot_service import bot_service
            if not bot_service._client:
                logger.warning("Bot client tayyor emas")
                return None
            stream = io.BytesIO(image)
            stream.name = "ob-havo.png"   # Telethon turini nomdan aniqlaydi
            msg = await bot_service._client.send_file(
                CHANNEL, stream, caption=caption, parse_mode="html",
            )
            logger.info(f"Kanal rasm posti yuborildi ({len(image) // 1024} KB)")
            return msg.id
        except Exception as e:
            logger.error(f"Kanal rasm posti xatosi: {e}")
            return None

    async def _save_channel_post(
        self, telegram_message_id: int, post_type: str, topic: str, text: str,
        category: str = "",
    ) -> None:
        """Kanalga yuborilgan postni DB ga saqlaydi.

        `category` — yangilik postlari uchun curation belgilagan kategoriya;
        keyingi kunlarda tanlov shu tarixga qarab xilma-xil bo'ladi.
        """
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
                    category=category or None,
                    text_preview=clean[:500].strip(),
                    views=0,
                )
                db.add(post)
                await db.commit()
                logger.info(f"ChannelPost saqlandi: id={telegram_message_id}, type={post_type}")
        except Exception as e:
            logger.error(f"ChannelPost saqlashda xato: {e}")

    async def _send_for_approval(
        self, text: str, post_type: str, topic: str = "", style: str = "",
        category: str = "",
    ) -> None:
        """Postni egaga tasdiqlash uchun botda ko'rsatadi."""
        from telethon import Button
        from app.services.bot_service import bot_service
        from app.database.redis import get_redis

        if not bot_service._client:
            logger.warning("Bot client tayyor emas — post egaga yuborib bo'lmadi")
            return

        # Sifat qatlami: muharrir-tanqid o'tkazib yakuniy versiyani olamiz
        # (xato bo'lsa critique_and_improve originalni qaytaradi).
        from app.services.news_fetcher import news_fetcher
        text = await news_fetcher.critique_and_improve(text, post_type)

        post_id = str(uuid.uuid4())[:8]
        text_with_footer = _md_to_html(text.rstrip()) + CHANNEL_FOOTER_HTML

        r = await get_redis()
        await r.setex(
            f"pending_post:{post_id}",
            86400,
            json.dumps({
                "text": text_with_footer, "post_type": post_type,
                "topic": topic, "style": style, "category": category,
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
        # Uslub va kategoriya saqlanadi — tahrirdan keyin ham post o'z
        # uslubida qoladi va tasdiqlanganda kategoriya tarixga yoziladi.
        await self._send_for_approval(
            new_text, data["post_type"], data.get("topic", ""),
            data.get("style", ""), data.get("category", ""),
        )

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
        from app.services.news_fetcher import DEFAULT_STYLE
        style = data.get("style") or DEFAULT_STYLE

        # Rad etilgan mavzu ham "yaqinda chiqqan" deb hisoblanadi — shunda
        # yangi tanlov ham eski mavzudan, ham kanal tarixidan qochadi.
        if post_type == "educational":
            recent = [old_topic] + await self._recent_topics("educational")
            new_topic = news_fetcher.get_todays_topic(recent)
            logger.info(f"Boshqa ta'limiy post: '{old_topic}' → '{new_topic}'")
            new_text = await news_fetcher.generate_educational_post(new_topic, style)
            await self._send_for_approval(new_text, "educational", new_topic, style)
        elif post_type == "practical":
            recent = [old_topic] + await self._recent_topics("practical")
            new_topic = news_fetcher.get_todays_practical_topic(recent)
            new_text = await news_fetcher.generate_practical_post(new_topic, style)
            await self._send_for_approval(new_text, "practical", new_topic, style)
        elif post_type == "tool":
            recent = [old_topic] + await self._recent_topics("tool")
            new_tool = news_fetcher.get_todays_tool(recent)
            new_text = await news_fetcher.generate_tool_review_post(new_tool, style)
            await self._send_for_approval(new_text, "tool", new_tool, style)
        elif post_type == "free":
            new_text = await news_fetcher.generate_free_post(old_topic, style)
            await self._send_for_approval(new_text, "free", old_topic, style)
        else:
            # Yangiliklar: tasodifiy tartib — curation boshqa yangilikni tanlaydi
            new_text, topic, category = await self._build_deep_news(style, shuffled=True)
            if new_text:
                await self._send_for_approval(new_text, "news", topic, style, category)

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

    # Haftalik kontent taqvimi: haftaning kuni → ertalabki post formati.
    # AI strategiya tavsiyasi: kuniga ko'pi bilan 2 post (09:00 format + 12:00 yangilik).
    WEEKLY_CALENDAR = {
        0: "educational",  # Dushanba
        1: "practical",    # Seshanba
        2: "tool",         # Chorshanba
        3: "educational",  # Payshanba
        4: "practical",    # Juma
    }

    async def post_by_calendar(self) -> None:
        """09:00 — haftalik taqvim bo'yicha format tanlab post tayyorlaydi."""
        import pytz
        from datetime import datetime
        from app.services.news_fetcher import news_fetcher, DEFAULT_STYLE

        weekday = datetime.now(pytz.timezone("Asia/Tashkent")).weekday()
        fmt = self.WEEKLY_CALENDAR.get(weekday)
        if not fmt:
            return
        try:
            if fmt == "practical":
                topic = news_fetcher.get_todays_practical_topic(
                    await self._recent_topics("practical")
                )
                logger.info(f"Taqvim: amaliy post — {topic}")
                text = await news_fetcher.generate_practical_post(topic, DEFAULT_STYLE)
                await self._send_for_approval(text, "practical", topic, DEFAULT_STYLE)
            elif fmt == "tool":
                tool = news_fetcher.get_todays_tool(await self._recent_topics("tool"))
                logger.info(f"Taqvim: vosita sharhi — {tool}")
                text = await news_fetcher.generate_tool_review_post(tool, DEFAULT_STYLE)
                await self._send_for_approval(text, "tool", tool, DEFAULT_STYLE)
            else:
                await self.post_educational()
        except Exception as e:
            logger.error(f"Taqvim posti xatosi ({fmt}): {e}")

    # ─── ertalabki ob-havo ──────────────────────────────────────────────────────

    async def post_weather(self) -> None:
        """07:00 — O'zbekiston ob-havosi. TasdiqlashSIZ to'g'ridan-to'g'ri kanalga.

        Nega tasdiqlash yo'q: bu faktik ma'lumot, har kuni ertalab tugma bosish
        ma'nosiz. Nega xavfsiz: raqamlar API dan keladi, LLM faqat bir gaplik
        maslahat yozadi. Ma'lumot olinmasa post UMUMAN yuborilmaydi — noto'g'ri
        ob-havodan ko'ra post bo'lmagani yaxshi.
        """
        from app.services import weather

        rows = await weather.fetch()
        if not rows:
            logger.warning("Ob-havo ma'lumoti olinmadi — post o'tkazib yuborildi")
            return

        comment = await self._weather_comment(rows)

        # Rasm bilan yuborishga urinamiz; chizilmasa oddiy matnli postga qaytamiz —
        # rasm hech qachon postni to'sib qo'ymasligi kerak.
        from app.services import weather_image
        image = weather_image.render(rows, weather.uzbek_date())

        if image:
            text = weather.caption(comment)
            message_id = await self._send_photo_to_channel(image, text)
        else:
            logger.warning("Ob-havo rasmi chizilmadi — matnli postga qaytildi")
            text = weather.format_post(rows, comment) + CHANNEL_FOOTER_HTML
            message_id = await self._send_to_channel(text)

        if not message_id:
            logger.error("Ob-havo posti kanalga yuborilmadi")
            return

        await self._save_channel_post(
            telegram_message_id=message_id,
            post_type="weather",
            topic=weather.uzbek_date(),
            text=weather.format_post(rows, comment),   # analitikada to'liq matn
        )
        logger.info(
            f"Ob-havo posti yuborildi ({len(rows)} shahar, "
            f"{'rasm bilan' if image else 'matn'})"
        )

    async def _weather_comment(self, rows: list[dict]) -> str:
        """Bir gaplik maslahat. Xato bo'lsa bo'sh — post baribir chiqadi.

        Modelga RAQAM YOZISH TAQIQLANADI: barcha sonlar postda allaqachon bor
        va ular API dan kelgan. Model raqam yozsa, u to'qima bo'lardi.
        """
        from app.services import weather
        from app.services.news_fetcher import news_fetcher

        facts = weather.summarize(rows)
        prompt = (
            "Bugungi O'zbekiston ob-havosi bo'yicha faktlar:\n"
            f"- Toshkent: {facts['toshkent']}\n"
            f"- Eng issiq: {facts['eng_issiq']}\n"
            f"- Eng salqin: {facts['eng_salqin']}\n"
            f"- Yomg'ir kutilayotgan shaharlar: "
            f"{', '.join(facts['yomgirli_shaharlar']) or 'yo`q'}\n\n"
            "Shu faktlarga qarab o'quvchiga BITTA gaplik amaliy maslahat yoz "
            "(masalan issiqda suv ichish, yomg'irda soyabon, salqinda kiyim).\n\n"
            "Qat'iy qoidalar:\n"
            "- RAQAM YOZMA (harorat, foiz, gradus) — ular postda allaqachon bor.\n"
            "- Bitta gap, 12 so'zdan oshmasin.\n"
            "- O'zbek lotin alifbosida, iliq va oddiy ohangda.\n"
            "- Faqat gapning o'zini qaytar, tirnoqsiz va izohsiz."
        )
        try:
            comment = await news_fetcher._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6, max_tokens=1024,
            )
        except Exception as e:
            logger.warning(f"Ob-havo izohi yozilmadi: {e}")
            return ""

        comment = comment.strip().strip('"').strip()
        # Model qoidani buzib raqam yozsa — izohni tashlaymiz, post raqamsiz chiqadi
        if any(ch.isdigit() for ch in comment):
            logger.warning(f"Ob-havo izohida raqam bor — tashlab yuborildi: {comment[:60]}")
            return ""
        return comment[:200]

    async def post_educational(self) -> None:
        """09:00 — AI ta'limiy post (egaga tasdiqlashga yuboriladi)."""
        try:
            from app.services.news_fetcher import news_fetcher, DEFAULT_STYLE
            topic = news_fetcher.get_todays_topic(await self._recent_topics("educational"))
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
            text, topic, category = await self._build_deep_news(DEFAULT_STYLE)
            if not text:
                logger.warning("Yangilik topilmadi — post o'tkazib yuborildi")
                return
            await self._send_for_approval(text, "news", topic, DEFAULT_STYLE, category)
        except Exception as e:
            logger.error(f"Yangiliklar post xatosi: {e}")

    async def _recent_news_posts(self) -> list[dict]:
        """Oxirgi kunlarda chiqqan yangilik postlari (eng yangisi birinchi).

        Curation shu tarixni ko'rib, o'sha yangilikni yoki o'sha kategoriyani
        qayta tanlamaydi — busiz kanalda bitta tema takrorlanib qolardi.
        """
        try:
            from datetime import datetime, timedelta, timezone
            from sqlalchemy import select, desc
            from app.database.session import AsyncSessionLocal
            from app.database.models import ChannelPost
            from app.services.news_fetcher import NEWS_HISTORY_DAYS

            since = datetime.now(timezone.utc) - timedelta(days=NEWS_HISTORY_DAYS)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChannelPost)
                    .where(
                        ChannelPost.post_type == "news",
                        ChannelPost.sent_at >= since,
                    )
                    .order_by(desc(ChannelPost.sent_at))
                    .limit(10)
                )
                posts = result.scalars().all()
            return [{"topic": p.topic or "", "category": p.category or ""} for p in posts]
        except Exception as e:
            logger.warning(f"Yangilik tarixini o'qishda xato — tarixsiz davom etadi: {e}")
            return []

    async def _recent_topics(self, post_type: str, limit: int = 40) -> list[str]:
        """Shu turdagi oxirgi postlar mavzusi (eng yangisi birinchi).

        Mavzu tanlash faqat sanaga bog'langan edi; kanal haftada bir necha post
        chiqargani uchun katalog tez tugab, mavzular qayta boshlanardi. Shu
        tarix `_pick_fresh` ga berilganda yaqinda chiqqan mavzu qayta tanlanmaydi.
        """
        try:
            from sqlalchemy import select, desc
            from app.database.session import AsyncSessionLocal
            from app.database.models import ChannelPost

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChannelPost.topic)
                    .where(ChannelPost.post_type == post_type)
                    .order_by(desc(ChannelPost.sent_at))
                    .limit(limit)
                )
                return [t for t in result.scalars().all() if t]
        except Exception as e:
            logger.warning(f"'{post_type}' tarixini o'qishda xato — tarixsiz davom etadi: {e}")
            return []

    async def _build_deep_news(
        self, style: str, shuffled: bool = False
    ) -> tuple[str | None, str, str]:
        """Keng yangilik ro'yxatidan eng muhimini tanlab, chuqur post tayyorlaydi.

        Returns: (post_matni | None, tanlangan sarlavha, kategoriya).
        """
        from app.services.news_fetcher import news_fetcher, NEWS_POOL_SIZE
        if shuffled:
            items = await news_fetcher.get_ai_news_shuffled(count=NEWS_POOL_SIZE)
        else:
            items = await news_fetcher.get_ai_news(count=NEWS_POOL_SIZE)
        recent = await self._recent_news_posts()
        picked = await news_fetcher.curate_top_news(items, recent=recent)
        if not picked:
            return None, "", ""
        item = picked["item"]
        category = picked.get("category", "")
        logger.info(
            f"Curation tanladi [{category}]: {item['title'][:70]} — {picked['reason'][:80]}"
        )
        text = await news_fetcher.generate_deep_news_post(
            item, style, curation_reason=picked["reason"]
        )
        return text, item["title"][:200], category

    async def create_on_demand(self, post_type: str, style: str, topic: str = "") -> None:
        """Bot menyusidan chaqiriladigan on-demand post yaratish (tur + uslub)."""
        try:
            from app.services.news_fetcher import news_fetcher
            category = ""
            if post_type == "educational":
                topic = topic or news_fetcher.get_todays_topic(
                    await self._recent_topics("educational")
                )
                text = await news_fetcher.generate_educational_post(topic, style)
            elif post_type == "news":
                text, topic, category = await self._build_deep_news(style)
                if not text:
                    await self._notify_owner_text("❌ Hozircha yangilik topilmadi — keyinroq urinib ko'ring.")
                    return
            elif post_type == "practical":
                topic = topic or news_fetcher.get_todays_practical_topic(
                    await self._recent_topics("practical")
                )
                text = await news_fetcher.generate_practical_post(topic, style)
            elif post_type == "tool":
                topic = topic or news_fetcher.get_todays_tool(
                    await self._recent_topics("tool")
                )
                text = await news_fetcher.generate_tool_review_post(topic, style)
            else:  # free — ega bergan mavzu
                text = await news_fetcher.generate_free_post(topic, style)
            await self._send_for_approval(text, post_type, topic, style, category)
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

    # ─── uslub o'rgatish (namuna kanaldan) ─────────────────────────────────────

    async def learn_style_from_channel(self, channel: str) -> dict | None:
        """Berilgan kanalning postlaridan yozish uslubini o'rganib saqlaydi.

        Userbot orqali oxirgi postlarni oladi, LLM uslub kartasini chiqaradi,
        eng yaxshi namunalar bilan birga AgentConfig'ga yozadi. Natija:
        {source, style_card, samples_count} yoki None.
        """
        from app.services.telegram_service import telegram_service
        from app.services.news_fetcher import news_fetcher

        channel = channel.strip()
        if channel.startswith("https://t.me/"):
            channel = "@" + channel.split("t.me/")[1].split("/")[0]
        if not channel.startswith("@"):
            channel = "@" + channel

        # 1. Postlarni yig'ish (matnli, yetarlicha uzun)
        try:
            messages = await telegram_service._client.get_messages(channel, limit=50)
        except Exception as e:
            logger.error(f"Uslub o'rganish: kanal o'qilmadi ({channel}): {e}")
            return None

        posts = [
            {"text": m.text, "views": m.views or 0}
            for m in messages
            if m and m.text and len(m.text) >= 200
        ]
        if len(posts) < 5:
            logger.warning(f"Uslub o'rganish: {channel} da yetarli matnli post yo'q ({len(posts)})")
            return None

        # Eng ko'p ko'rilgan 15 tasi tahlilga, top 3 namuna sifatida saqlanadi
        posts.sort(key=lambda p: p["views"], reverse=True)
        analysis_posts = posts[:15]
        # Namunalardan kanal imzosi/username qatorlarini tozalaymiz — aks holda
        # model ularni yangi postga ko'chirib qo'yadi (begona kanal reklamasi).
        signature_re = re.compile(
            r"^\s*(?:[@🔗📢▪️•➡️—-]\s*)*(?:@\w+|https?://t\.me/\S+)\s*$", re.MULTILINE
        )
        samples = [signature_re.sub("", p["text"][:1200]).strip() for p in posts[:3]]

        # 2. LLM uslub kartasi
        joined = "\n\n---POST---\n\n".join(p["text"][:800] for p in analysis_posts)
        style_card = await news_fetcher.analyze_style(joined)
        if not style_card:
            return None

        # 3. Saqlash (AgentConfig — doimiy, dashboard orqali ham ko'rinadi)
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.database.session import AsyncSessionLocal
        from app.database.models import AgentConfig

        payload = json.dumps({
            "source": channel,
            "style_card": style_card,
            "samples": samples,
            "learned_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)

        async with AsyncSessionLocal() as db:
            r = await db.execute(select(AgentConfig).where(AgentConfig.key == "learned_style"))
            cfg = r.scalar_one_or_none()
            if cfg:
                cfg.value = payload
            else:
                db.add(AgentConfig(
                    key="learned_style", value=payload,
                    description="Namuna kanaldan o'rganilgan post uslubi",
                ))
            await db.commit()

        # Keshni yangilaymiz — keyingi generatsiya darhol yangi uslubni ko'radi
        news_fetcher.set_learned_style_cache(json.loads(payload))
        logger.info(f"Uslub o'rganildi: {channel} ({len(analysis_posts)} post tahlil qilindi)")
        return {"source": channel, "style_card": style_card, "samples_count": len(samples)}

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

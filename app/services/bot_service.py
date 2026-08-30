import json
import re
from datetime import date

from loguru import logger
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError

from app.config import settings
from app.repositories.task_repo import (
    TASK_EMOJIS, TASK_NAMES,
    get_today_tasks, create_task, mark_done, delete_task,
)

MAIN_BUTTONS = [
    [Button.inline("🤝 Yig'ilish",    b"add:meeting"),
     Button.inline("🎤 Konferensiya", b"add:conference")],
    [Button.inline("📚 Dars",         b"add:class"),
     Button.inline("✅ Boshqa",        b"add:other")],
    [Button.inline("📋 Bugungi reja", b"view:today")],
    [Button.inline("📢 Kanal posti", b"newpost:menu"),
     Button.inline("🧠 Bilim bazasi", b"faq:menu")],
    [Button.inline("💡 Ikkinchi miya", b"note:menu")],
]

CANCEL_ROW = [[Button.inline("❌ Bekor qilish", b"cancel")]]

FAQ_BUTTONS = [
    [Button.inline("➕ Yangi FAQ qo'shish", b"faq:add")],
    [Button.inline("📋 FAQ ro'yxati", b"faq:list")],
]

NEWPOST_TYPE_BUTTONS = [
    [Button.inline("🎓 Ta'limiy", b"newpost:type:educational"),
     Button.inline("🌐 Yangilik", b"newpost:type:news")],
    [Button.inline("🛠 Amaliy qo'llanma", b"newpost:type:practical"),
     Button.inline("🧰 Vosita sharhi", b"newpost:type:tool")],
    [Button.inline("✍️ Erkin mavzu", b"newpost:type:free")],
    [Button.inline("❓ Quiz", b"newpost:type:quiz"),
     Button.inline("📊 So'rovnoma", b"newpost:type:poll")],
    [Button.inline("🎯 Uslub o'rgatish", b"newpost:learnstyle")],
    [Button.inline("🔙 Menyu", b"menu")],
]


class BotService:
    def __init__(self) -> None:
        self._client: TelegramClient | None = None
        self._owner_id: int = settings.owner_telegram_id

    # ─── startup ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not settings.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN yo'q — bot service o'chirilgan")
            return
        self._client = TelegramClient(
            "sessions/bot_session",
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await self._client.start(bot_token=settings.telegram_bot_token)
        self._register_handlers()
        await self._set_commands()
        me = await self._client.get_me()
        logger.info(f"Bot ulandi: @{me.username}")

    async def _set_commands(self) -> None:
        from telethon.tl.functions.bots import SetBotCommandsRequest
        from telethon.tl.types import BotCommand, BotCommandScopeDefault
        await self._client(SetBotCommandsRequest(
            scope=BotCommandScopeDefault(),
            lang_code="",
            commands=[
                BotCommand(command="menu",  description="📅 Kunlik reja menyusi"),
                BotCommand(command="reja",  description="📋 Bugungi reja ko'rish"),
                BotCommand(command="faq",   description="🧠 Bilim bazasi (FAQ)"),
                BotCommand(command="start", description="🚀 Botni ishga tushirish"),
            ],
        ))

    def _register_handlers(self) -> None:
        owner = self._owner_id

        @self._client.on(events.NewMessage(from_users=owner, incoming=True))
        async def on_msg(event):
            await self._handle_message(event)

        @self._client.on(events.CallbackQuery)
        async def on_cb(event):
            if event.sender_id != owner:
                await event.answer("Ruxsat yo'q", alert=True)
                return
            await self._handle_callback(event)

    # ─── state ────────────────────────────────────────────────────────────────

    async def _get_state(self) -> dict | None:
        from app.database.redis import get_redis
        r = await get_redis()
        raw = await r.get(f"bot_state:{self._owner_id}")
        return json.loads(raw) if raw else None

    async def _set_state(self, state: dict | None) -> None:
        from app.database.redis import get_redis
        r = await get_redis()
        key = f"bot_state:{self._owner_id}"
        if state is None:
            await r.delete(key)
        else:
            await r.setex(key, 600, json.dumps(state))

    # ─── message handler ──────────────────────────────────────────────────────

    async def _handle_message(self, event) -> None:
        text = (event.message.text or "").strip()
        if not text:
            return

        if text in ("/faq",):
            await self._send_faq_menu(event.chat_id)
            return

        if text.startswith("/qidir"):
            query = text[len("/qidir"):].strip()
            if not query:
                await self._set_state({"step": "note_search"})
                await self._client.send_message(
                    event.chat_id, "🔎 Nimani qidiray?", buttons=CANCEL_ROW)
            else:
                await self._search_notes(event.chat_id, query)
            return

        if text in ("/miya", "/eslatma"):
            await self._send_note_menu(event.chat_id)
            return

        if text in ("/start", "/menu", "/reja", "/plan", "/help"):
            await self._send_main_menu(event.chat_id)
            return

        state = await self._get_state()
        if not state:
            # Holat yo'q va buyruq emas → bu ESLATMA. Ikkinchi miyaning
            # asosiy kirish nuqtasi shu: menyu bosish shart emas, shunchaki
            # yozasiz. Ilgari bu yerda menyu qaytarilardi.
            await self._save_note(event, text)
            return

        step = state.get("step")

        if step == "note_search":
            await self._set_state(None)
            await self._search_notes(event.chat_id, text)
            return

        if step == "relaying":
            await self._handle_relay(event, state, text)
            return

        if step == "newpost_topic":
            state.update(step="newpost_style", topic=text)
            await self._set_state(state)
            await self._client.send_message(
                event.chat_id,
                f"✍️ Mavzu: {text}\n\n🎨 Uslubni tanlang:",
                buttons=await self._style_buttons("newpost:genfree"),
            )
            return

        if step == "learn_style":
            await self._set_state(None)
            await self._client.send_message(
                event.chat_id,
                f"🔍 {text} kanali o'rganilmoqda — postlarni o'qib, uslubni tahlil qilaman "
                "(30-60 soniya)...",
            )
            import asyncio as _aio
            _aio.create_task(self._learn_style_and_report(event.chat_id, text))
            return

        if step == "quiz_topic":
            kind = state.get("kind", "quiz")
            await self._set_state(None)
            nom = "Quiz" if kind == "quiz" else "So'rovnoma"
            await self._client.send_message(
                event.chat_id, f"⏳ {nom} tayyorlanmoqda — tasdiqlash uchun keladi..."
            )
            import asyncio as _aio
            from app.services.channel_poster import channel_poster
            _aio.create_task(channel_poster.create_quiz_on_demand(kind, text))
            return

        if step == "faq_question":
            state.update(step="faq_answer", question=text)
            await self._set_state(state)
            await self._client.send_message(
                event.chat_id,
                f"❓ Savol: {text}\n\n✍️ Endi shu savolga JAVOBni yozing:",
                buttons=CANCEL_ROW,
            )
            return

        if step == "faq_answer":
            question = state.get("question", "")
            await self._set_state(None)
            await self._client.send_message(event.chat_id, "⏳ Saqlanmoqda va indekslanmoqda...")
            from app.services.faq_service import faq_service
            await faq_service.add_faq(question, text)
            await self._client.send_message(
                event.chat_id,
                "✅ FAQ qo'shildi. Endi agent shunga o'xshash savollarga o'zi javob beradi.",
                buttons=[[Button.inline("🧠 Bilim bazasi", b"faq:menu")]],
            )
            return

        if step == "editing_post":
            post_id = state.get("post_id", "")
            await self._set_state(None)
            await self._client.send_message(
                event.chat_id, "⏳ Post qayta tayyorlanmoqda..."
            )
            from app.services.channel_poster import channel_poster
            await channel_poster.regenerate_and_send_for_approval(post_id, text)
            return

        if step == "waiting_time":
            parsed = self._parse_time(text)
            if not parsed:
                await self._client.send_message(
                    event.chat_id,
                    "❌ Format noto'g'ri. Misol:\n"
                    "• <code>10:30</code>\n"
                    "• <code>10:30-12:00</code>",
                    parse_mode="html",
                    buttons=CANCEL_ROW,
                )
                return
            start, end = parsed
            state.update(step="waiting_title", start_time=start, end_time=end)
            await self._set_state(state)
            t = f"{start}" + (f"–{end}" if end else "")
            await self._client.send_message(
                event.chat_id,
                f"✅ Vaqt: <b>{t}</b>\n\n📝 Sarlavha kiriting (ixtiyoriy):",
                parse_mode="html",
                buttons=[
                    [Button.inline("⏩ O'tkazib yuborish", b"skip:title"),
                     Button.inline("❌ Bekor", b"cancel")],
                ],
            )

        elif step == "waiting_title":
            await self._save_and_confirm(event.chat_id, state, title=text)

    # ─── callback handler ─────────────────────────────────────────────────────

    async def _handle_callback(self, event) -> None:
        data = event.data.decode()
        await event.answer()

        if data == "menu":
            await event.edit("📅 Kunlik reja:", buttons=MAIN_BUTTONS)

        elif data == "view:today":
            await self._show_today(event)

        elif data.startswith("add:"):
            task_type = data.split(":")[1]
            await self._set_state({"step": "waiting_time", "type": task_type})
            emoji = TASK_EMOJIS[task_type]
            name = TASK_NAMES[task_type]
            await event.edit(
                f"{emoji} <b>{name}</b> qo'shilmoqda\n\n"
                "🕐 Vaqtni kiriting:\n"
                "• <code>10:30</code> — faqat boshlanish\n"
                "• <code>10:30-12:00</code> — boshlanish va tugash",
                parse_mode="html",
                buttons=CANCEL_ROW,
            )

        elif data == "skip:title":
            state = await self._get_state()
            if state and state.get("step") == "waiting_title":
                await self._save_and_confirm(None, state, title=None)
                await event.edit("⏩ O'tkazib yuborildi.")

        elif data == "cancel":
            await self._set_state(None)
            await event.edit("❌ Bekor qilindi.", buttons=[[Button.inline("🔙 Menyu", b"menu")]])

        elif data.startswith("relay:"):
            target_id = int(data.split(":", 1)[1])
            name = await self._lookup_user_name(target_id)
            await self._set_state(
                {"step": "relaying", "target_id": target_id, "target_name": name}
            )
            await self._client.send_message(
                event.chat_id,
                f"✍️ {name} ({target_id}) ga javobingizni yozing:",
                buttons=CANCEL_ROW,
            )

        elif data == "note:menu":
            await self._show_note_menu(event)

        elif data == "note:search":
            await self._set_state({"step": "note_search"})
            await event.edit("🔎 Nimani qidiray?\n\nSavolni odatiy tilda yozing — "
                             "masalan «RAG haqida nima yozgandim?»", buttons=CANCEL_ROW)

        elif data == "note:recent":
            await self._show_recent_notes(event)

        elif data.startswith("note_open:"):
            await self._open_note(event, int(data.split(":", 1)[1]))

        elif data == "faq:menu":
            await self._show_faq_menu(event)

        elif data == "faq:add":
            await self._set_state({"step": "faq_question"})
            await event.edit(
                "🧠 Yangi FAQ qo'shish\n\n❓ Avval SAVOLni yozing "
                "(foydalanuvchilar qanday so'rashi mumkin):",
                buttons=CANCEL_ROW,
            )

        elif data == "faq:list":
            await self._show_faq_list(event)

        elif data.startswith("faq_del:"):
            faq_id = int(data.split(":", 1)[1])
            from app.services.faq_service import faq_service
            await faq_service.remove_faq(faq_id)
            await self._show_faq_list(event)

        elif data == "newpost:menu":
            await event.edit("📢 Qanday post yaratamiz?", buttons=NEWPOST_TYPE_BUTTONS)

        elif data == "newpost:learnstyle":
            await self._set_state({"step": "learn_style"})
            await event.edit(
                "🎯 Qaysi kanalning yozish uslubini o'rganay?\n\n"
                "Kanal linkini yoki @username ni yuboring (ochiq kanal bo'lsin):",
                buttons=CANCEL_ROW,
            )

        elif data.startswith("newpost:type:"):
            ptype = data.split(":")[2]
            if ptype == "free":
                await self._set_state({"step": "newpost_topic"})
                await event.edit(
                    "✍️ Post mavzusi yoki topshiriqni yozing (masalan: "
                    "\"AI bilan biznesni avtomatlashtirish\"):",
                    buttons=CANCEL_ROW,
                )
            elif ptype in ("quiz", "poll"):
                await self._set_state({"step": "quiz_topic", "kind": ptype})
                nom = "❓ Quiz savoli" if ptype == "quiz" else "📊 So'rovnoma"
                await event.edit(
                    f"{nom} qaysi mavzuda bo'lsin? Mavzuni yozing "
                    "(masalan: \"RAG\", \"Prompt engineering\", \"AI xavfsizligi\"):",
                    buttons=CANCEL_ROW,
                )
            else:
                await event.edit(
                    "🎨 Uslubni tanlang:", buttons=await self._style_buttons(f"newpost:gen:{ptype}")
                )

        elif data.startswith("approve_poll:"):
            await self._approve_poll(event, data.split(":", 1)[1])

        elif data.startswith("regen_poll:"):
            poll_id = data.split(":", 1)[1]
            await event.edit("🔄 Yangi variant tayyorlanmoqda...")
            import asyncio as _aio
            from app.services.channel_poster import channel_poster
            _aio.create_task(channel_poster.regenerate_poll(poll_id))

        elif data.startswith("reject_poll:"):
            poll_id = data.split(":", 1)[1]
            from app.database.redis import get_redis
            r = await get_redis()
            await r.delete(f"pending_poll:{poll_id}")
            await event.edit("🗑 Rad etildi.")

        elif data.startswith("newpost:gen:"):
            _, _, ptype, style = data.split(":")
            await event.edit("⏳ Post tayyorlanmoqda — tasdiqlash uchun alohida keladi...")
            import asyncio as _aio
            from app.services.channel_poster import channel_poster
            _aio.create_task(channel_poster.create_on_demand(ptype, style))

        elif data.startswith("newpost:genfree:"):
            style = data.split(":")[2]
            state = await self._get_state()
            topic = (state or {}).get("topic", "")
            await self._set_state(None)
            await event.edit("⏳ Post tayyorlanmoqda — tasdiqlash uchun alohida keladi...")
            import asyncio as _aio
            from app.services.channel_poster import channel_poster
            _aio.create_task(channel_poster.create_on_demand("free", style, topic))

        elif data.startswith("approve_post:"):
            post_id = data.split(":", 1)[1]
            await self._approve_post(event, post_id)

        elif data.startswith("edit_post:"):
            post_id = data.split(":", 1)[1]
            await self._set_state({"step": "editing_post", "post_id": post_id})
            await event.edit(
                "✏️ Qanday o'zgartirish kerak? Izohingizni yozing:",
                buttons=[[Button.inline("❌ Bekor qilish", b"cancel")]],
            )

        elif data.startswith("regen_post:"):
            post_id = data.split(":", 1)[1]
            await event.edit("🔄 Yangi post tayyorlanmoqda...")
            import asyncio as _aio
            from app.services.channel_poster import channel_poster
            _aio.create_task(channel_poster.regenerate_new_and_send_for_approval(post_id))

        elif data.startswith("reject_post:"):
            post_id = data.split(":", 1)[1]
            await self._reject_post(event, post_id)

        elif data.startswith("done:"):
            from app.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await mark_done(db, int(data.split(":")[1]))
            await self._show_today(event)

        elif data.startswith("del:"):
            from app.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await delete_task(db, int(data.split(":")[1]))
            await self._show_today(event)

    # ─── helpers ──────────────────────────────────────────────────────────────

    async def _save_and_confirm(self, chat_id, state: dict, title: str | None) -> None:
        from app.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            task = await create_task(
                db,
                task_type=state["type"],
                start_time=state.get("start_time"),
                end_time=state.get("end_time"),
                title=title,
            )
        await self._set_state(None)

        emoji = TASK_EMOJIS[state["type"]]
        name = TASK_NAMES[state["type"]]
        t = state.get("start_time", "")
        if state.get("end_time"):
            t += f"–{state['end_time']}"
        title_str = f" — {title}" if title else ""

        target = chat_id or self._owner_id
        await self._client.send_message(
            target,
            f"✅ Saqlandi: {emoji} <b>{name}</b> {t}{title_str}\n\nYangi qo'shish:",
            parse_mode="html",
            buttons=MAIN_BUTTONS,
        )
        logger.info(f"Vazifa saqlandi: {name} {t}")

    async def _show_today(self, event) -> None:
        from app.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            tasks = await get_today_tasks(db)

        today_str = date.today().strftime("%-d-%B, %A")

        if not tasks:
            text = f"📅 <b>Bugungi reja</b> ({today_str})\n\n💤 Hali vazifalar yo'q."
            buttons = [[Button.inline("➕ Qo'shish", b"menu")]]
        else:
            lines = [f"📅 <b>Bugungi reja</b> ({today_str})\n"]
            action_rows = []
            for i, t in enumerate(tasks, 1):
                emoji = TASK_EMOJIS.get(t.task_type, "•")
                time_str = t.start_time or ""
                if t.end_time:
                    time_str += f"–{t.end_time}"
                title = f" — {t.title}" if t.title else ""
                done = "✅" if t.is_done else "⬜"
                name = TASK_NAMES.get(t.task_type, t.task_type)
                lines.append(f"{done} {time_str} {emoji} {name}{title}")

                row = []
                if not t.is_done:
                    row.append(Button.inline(f"✅ {i}", f"done:{t.id}".encode()))
                row.append(Button.inline(f"🗑 {i}", f"del:{t.id}".encode()))
                action_rows.append(row)

            text = "\n".join(lines)
            buttons = action_rows + [[Button.inline("➕ Yangi qo'shish", b"menu")]]

        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except (MessageNotModifiedError, AttributeError):
            await self._client.send_message(
                self._owner_id, text, buttons=buttons, parse_mode="html"
            )

    async def _approve_post(self, event, post_id: str) -> None:
        """Tasdiqlangan postni kanalga yuboradi va DB ga saqlaydi."""
        import json
        from app.database.redis import get_redis
        from app.services.channel_poster import channel_poster

        r = await get_redis()
        raw = await r.get(f"pending_post:{post_id}")
        if not raw:
            await event.edit("❌ Post topilmadi yoki muddati o'tgan.")
            return

        data = json.loads(raw)
        await r.delete(f"pending_post:{post_id}")
        telegram_message_id = await channel_poster._send_to_channel(data["text"])
        if telegram_message_id:
            await channel_poster._save_channel_post(
                telegram_message_id=telegram_message_id,
                post_type=data.get("post_type", "news"),
                topic=data.get("topic", ""),
                text=data["text"],
                category=data.get("category", ""),
            )
            await event.edit("✅ Post kanalga yuborildi!")
        else:
            await event.edit("❌ Kanalga yuborishda xato yuz berdi.")

    async def _reject_post(self, event, post_id: str) -> None:
        """Postni rad etadi va Redis dan o'chiradi."""
        from app.database.redis import get_redis
        r = await get_redis()
        raw = await r.get(f"pending_post:{post_id}")
        if not raw:
            await event.edit("❌ Post topilmadi yoki allaqachon o'chirilgan.")
            return
        await r.delete(f"pending_post:{post_id}")
        logger.info(f"Post rad etildi: id={post_id}")
        await event.edit("🗑 Post rad etildi va o'chirildi.")

    # ─── ikki tomonlama relay ──────────────────────────────────────────────────

    async def _handle_relay(self, event, state: dict, text: str) -> None:
        """Eganing yozgan javobini foydalanuvchiga yetkazadi va tasdiqlaydi."""
        target_id = state.get("target_id")
        target_name = state.get("target_name") or str(target_id)
        await self._set_state(None)

        if not target_id:
            await self._client.send_message(event.chat_id, "❌ Qabul qiluvchi topilmadi.")
            return

        ok = await self.relay_reply(int(target_id), text)
        if ok:
            await self._client.send_message(
                event.chat_id, f"✅ {target_name} ga javobingiz yuborildi."
            )
        else:
            await self._client.send_message(
                event.chat_id,
                f"❌ {target_name} ga yuborishda xato. Keyinroq qayta urinib ko'ring.",
            )

    async def relay_reply(self, target_id: int, text: str) -> bool:
        """Eganing javobini userbot orqali (Shaxzodbek akkauntidan) yetkazadi."""
        from app.services.telegram_service import telegram_service
        try:
            await telegram_service.send_message(target_id, text)
            logger.info(f"Relay: egadan {target_id} ga javob yuborildi")
            return True
        except Exception as e:
            logger.error(f"Relay xatosi ({target_id}): {e}")
            return False

    async def _lookup_user_name(self, telegram_id: int) -> str:
        """telegram_id bo'yicha foydalanuvchi ismini topadi (topilmasa id qaytaradi)."""
        from sqlalchemy import select
        from app.database.session import AsyncSessionLocal
        from app.database.models import TelegramUser
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
                )
                u = r.scalar_one_or_none()
                return u.display_name if u else str(telegram_id)
        except Exception:
            return str(telegram_id)

    # ─── kanal posti yaratish ───────────────────────────────────────────────────

    async def _style_buttons(self, action_prefix: str) -> list:
        """Uslub tanlash tugmalari. action_prefix — masalan 'newpost:gen:educational'
        yoki 'newpost:genfree'; oxiriga :{style} qo'shiladi."""
        from app.services.news_fetcher import POST_STYLES, news_fetcher
        rows = []
        # O'rganilgan uslub mavjud bo'lsa — birinchi (tavsiya) o'rinda
        learned = await news_fetcher.get_learned_style()
        if learned:
            src = learned.get("source", "")
            rows.append([Button.inline(f"🎯 O'rganilgan ({src})", f"{action_prefix}:learned".encode())])
        for key, meta in POST_STYLES.items():
            rows.append([Button.inline(meta["label"], f"{action_prefix}:{key}".encode())])
        rows.append([Button.inline("❌ Bekor qilish", b"cancel")])
        return rows

    async def _learn_style_and_report(self, chat_id, channel: str) -> None:
        """Uslubni o'rganib, natijani egaga hisobot qiladi (fon vazifasi)."""
        try:
            from app.services.channel_poster import channel_poster
            result = await channel_poster.learn_style_from_channel(channel)
        except Exception as e:
            logger.error(f"Uslub o'rganishda xato: {e}")
            result = None

        if not result:
            await self._client.send_message(
                chat_id,
                "❌ O'rganib bo'lmadi. Kanal ochiqligini va kamida 5 ta matnli "
                "post borligini tekshirib, qayta urinib ko'ring.",
            )
            return

        card_preview = result["style_card"][:600]
        await self._client.send_message(
            chat_id,
            f"✅ {result['source']} uslubi o'rganildi!\n\n"
            f"📋 Uslub kartasi (qisqacha):\n{card_preview}...\n\n"
            f"Endi post yaratishda \"🎯 O'rganilgan\" uslubini tanlang.",
            buttons=[[Button.inline("📢 Post yaratish", b"newpost:menu")]],
        )

    async def _approve_poll(self, event, poll_id: str) -> None:
        """Tasdiqlangan quiz/so'rovnomani kanalga native poll sifatida yuboradi."""
        import json as _json
        from app.database.redis import get_redis
        from app.services.channel_poster import channel_poster

        r = await get_redis()
        raw = await r.get(f"pending_poll:{poll_id}")
        if not raw:
            await event.edit("❌ Topilmadi yoki muddati o'tgan.")
            return
        quiz = _json.loads(raw)
        await r.delete(f"pending_poll:{poll_id}")

        message_id = await channel_poster._send_poll_to_channel(quiz)
        if message_id:
            await channel_poster._save_channel_post(
                telegram_message_id=message_id,
                post_type=quiz.get("kind", "quiz"),
                topic=quiz.get("topic", ""),
                text=quiz.get("question", ""),
            )
            await event.edit("✅ Kanalga yuborildi!")
        else:
            await event.edit("❌ Kanalga yuborishda xato yuz berdi.")

    # ─── ikkinchi miya ──────────────────────────────────────────────────────────

    NOTE_BUTTONS = [
        [Button.inline("🔎 Qidirish", b"note:search")],
        [Button.inline("🕐 So'nggilari", b"note:recent")],
        [Button.inline("🔙 Menyu", b"menu")],
    ]

    async def _note_menu_text(self) -> str:
        from app.services.notes import note_service
        try:
            st = await note_service.stats()
        except Exception:
            st = {"jami": 0, "qatlamlar": {}}
        tiers = st.get("qatlamlar", {})
        order = [("core", "yodda"), ("active", "faol"), ("warm", "iliq"),
                 ("cold", "sovuq"), ("archive", "arxiv")]
        lines = [f"💡 <b>Ikkinchi miya</b> — {st['jami']} ta eslatma\n"]
        if st["jami"]:
            for key, label in order:
                if tiers.get(key):
                    lines.append(f"  {label}: {tiers[key]}")
            lines.append("")
        lines.append(
            "Menyusiz ham ishlaydi: shu yerga <b>shunchaki yozing</b> — "
            "fikr, havola yoki eslatma saqlanadi.\n"
            "Qidirish: <code>/qidir savol</code>"
        )
        return "\n".join(lines)

    async def _send_note_menu(self, chat_id) -> None:
        await self._client.send_message(
            chat_id, await self._note_menu_text(),
            parse_mode="html", buttons=self.NOTE_BUTTONS)

    async def _show_note_menu(self, event) -> None:
        try:
            await event.edit(await self._note_menu_text(),
                             parse_mode="html", buttons=self.NOTE_BUTTONS)
        except MessageNotModifiedError:
            pass

    async def _save_note(self, event, text: str) -> None:
        """Holatsiz matn — eslatma sifatida saqlanadi."""
        from app.services.notes import note_service

        note = await note_service.save(text)
        if not note:
            return
        kinds = {"fikr": "💭", "maqola": "📄", "uchrashuv": "🤝",
                 "shaxs": "👤", "loyiha": "🚀"}
        emoji = kinds.get(note["kind"], "💭")
        body = [f"{emoji} <b>{note['title']}</b>"]
        if note.get("summary"):
            body.append(f"\n{note['summary']}")
        await self._client.send_message(
            event.chat_id, "\n".join(body), parse_mode="html",
            buttons=[[Button.inline("💡 Ikkinchi miya", b"note:menu"),
                      Button.inline("📅 Menyu", b"menu")]],
        )

    async def _search_notes(self, chat_id, query: str) -> None:
        from app.services.notes import note_service

        found = await note_service.search(query, limit=5)
        if not found:
            await self._client.send_message(
                chat_id, f"🔎 «{query}» bo'yicha eslatma topilmadi.",
                buttons=[[Button.inline("💡 Ikkinchi miya", b"note:menu")]])
            return

        lines = [f"🔎 <b>«{query}»</b> — {len(found)} ta topildi\n"]
        rows = []
        for i, n in enumerate(found, 1):
            when = n["created_at"].strftime("%d.%m.%Y")
            lines.append(f"{i}. <b>{n['title']}</b>  <i>{when}</i>")
            if n.get("summary"):
                lines.append(f"   {n['summary'][:120]}")
            rows.append([Button.inline(f"{i}. ochish", f"note_open:{n['id']}".encode())])

        await self._client.send_message(
            chat_id, "\n".join(lines), parse_mode="html",
            buttons=rows + [[Button.inline("💡 Ikkinchi miya", b"note:menu")]])

    async def _open_note(self, event, note_id: int) -> None:
        """To'liq matnni ko'rsatadi. Bu HAQIQIY teginish — so'nish sekinlashadi."""
        from sqlalchemy import select
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal
        from app.services.notes import note_service

        async with AsyncSessionLocal() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalar_one_or_none()
        if not note:
            await event.answer("Topilmadi", alert=True)
            return

        await note_service.touch(note_id)   # ochish = ishlatish

        when = note.created_at.strftime("%d.%m.%Y")
        text = (f"<b>{note.title}</b>\n<i>{note.kind} · {when}</i>\n\n"
                f"{note.body[:3000]}")
        if note.source_url:
            text += f"\n\n🔗 {note.source_url}"
        try:
            await event.edit(text, parse_mode="html",
                             buttons=[[Button.inline("💡 Ikkinchi miya", b"note:menu")]])
        except MessageNotModifiedError:
            pass

    async def _show_recent_notes(self, event) -> None:
        from sqlalchemy import select, desc
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal
        from app.services.notes import tier

        async with AsyncSessionLocal() as db:
            notes = (await db.execute(
                select(Note).order_by(desc(Note.created_at)).limit(10)
            )).scalars().all()

        if not notes:
            await event.edit("Hali eslatma yo'q. Shu yerga shunchaki yozing.",
                             buttons=self.NOTE_BUTTONS)
            return

        labels = {"core": "yodda", "active": "faol", "warm": "iliq",
                  "cold": "sovuq", "archive": "arxiv"}
        lines = ["🕐 <b>So'nggi eslatmalar</b>\n"]
        rows = []
        for i, n in enumerate(notes, 1):
            t = labels.get(tier(n.kind, n.access_count, n.last_touched, n.pinned), "")
            lines.append(f"{i}. <b>{n.title}</b>  <i>{n.created_at.strftime('%d.%m')} · {t}</i>")
            rows.append([Button.inline(f"{i}. ochish", f"note_open:{n.id}".encode())])

        try:
            await event.edit("\n".join(lines), parse_mode="html",
                             buttons=rows + [[Button.inline("🔙", b"note:menu")]])
        except MessageNotModifiedError:
            pass

    # ─── bilim bazasi (FAQ) ─────────────────────────────────────────────────────

    async def _faq_count(self) -> int:
        from app.database.session import AsyncSessionLocal
        from app.repositories.faq_repo import list_faqs
        async with AsyncSessionLocal() as db:
            return len(await list_faqs(db))

    def _faq_menu_text(self, count: int) -> str:
        return (
            f"🧠 **Bilim bazasi (FAQ)**\n\n"
            f"Hozir {count} ta savol-javob bor. Agent shu bilimlar asosida "
            f"foydalanuvchilarga o'xshash savollarga o'zi javob beradi "
            f"(egaga yo'naltirmasdan)."
        )

    async def _send_faq_menu(self, chat_id) -> None:
        count = await self._faq_count()
        await self._client.send_message(
            chat_id, self._faq_menu_text(count), parse_mode="md", buttons=FAQ_BUTTONS
        )

    async def _show_faq_menu(self, event) -> None:
        count = await self._faq_count()
        try:
            await event.edit(
                self._faq_menu_text(count), parse_mode="md", buttons=FAQ_BUTTONS
            )
        except MessageNotModifiedError:
            pass

    async def _show_faq_list(self, event) -> None:
        from app.database.session import AsyncSessionLocal
        from app.repositories.faq_repo import list_faqs
        async with AsyncSessionLocal() as db:
            faqs = await list_faqs(db)

        if not faqs:
            await event.edit(
                "📋 Hali FAQ yo'q. Yangi savol-javob qo'shing:",
                buttons=[[Button.inline("➕ Qo'shish", b"faq:add")],
                         [Button.inline("🔙 Menyu", b"faq:menu")]],
            )
            return

        # parse_mode ishlatilmaydi — foydalanuvchi kiritgan savol matni markdown
        # belgilarini o'z ichiga olishi mumkin.
        lines = ["📋 FAQ ro'yxati:\n"]
        del_rows = []
        for i, f in enumerate(faqs[:20], 1):
            q = f.question if len(f.question) <= 60 else f.question[:57] + "..."
            lines.append(f"{i}. {q}")
            del_rows.append([Button.inline(f"🗑 {i}-ni o'chirish", f"faq_del:{f.id}".encode())])
        buttons = del_rows + [[Button.inline("🔙 Menyu", b"faq:menu")]]
        try:
            await event.edit("\n".join(lines), buttons=buttons)
        except MessageNotModifiedError:
            pass

    async def _send_main_menu(self, chat_id) -> None:
        from app.database.session import AsyncSessionLocal
        from sqlalchemy import select, func
        from app.database.models import DailyTask

        today = date.today()
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(func.count(DailyTask.id)).where(DailyTask.date == today)
            )
            count = r.scalar() or 0

        today_str = today.strftime("%-d %B, %A")
        count_info = f"\n📌 Bugun {count} ta vazifa" if count else "\n📌 Bugun hali vazifalar yo'q"

        await self._client.send_message(
            chat_id,
            f"📅 <b>Kunlik reja — {today_str}</b>{count_info}\n\nQo'shmoqchi bo'lgan vazifangizni tanlang:",
            parse_mode="html",
            buttons=MAIN_BUTTONS,
        )

    # ─── morning reminder ─────────────────────────────────────────────────────

    async def send_morning_reminder(self) -> None:
        if not self._client:
            return
        try:
            from app.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                tasks = await get_today_tasks(db)

            today_str = date.today().strftime("%-d %B, %A")

            if tasks:
                lines = []
                for t in tasks:
                    emoji = TASK_EMOJIS.get(t.task_type, "•")
                    time_str = t.start_time or ""
                    if t.end_time:
                        time_str += f"–{t.end_time}"
                    title = f" — {t.title}" if t.title else ""
                    lines.append(f"• {time_str} {emoji}{title}")
                text = (
                    f"🌅 <b>Xayrli tong!</b> Bugun {today_str}\n\n"
                    f"📋 Oldingi reja:\n" + "\n".join(lines) +
                    "\n\nYangi vazifa qo'shish yoki tahrirlash:"
                )
            else:
                text = (
                    f"🌅 <b>Xayrli tong!</b> Bugun {today_str}\n\n"
                    f"Bugungi ish rejanggizni belgilang:"
                )

            # Ikkinchi miyadan eslatmalar — bu halqa bo'lmasa baza o'lik arxiv
            text += await self._resurfaced_block()

            await self._client.send_message(
                self._owner_id, text, parse_mode="html", buttons=MAIN_BUTTONS
            )
            logger.info("Ertalabki eslatma yuborildi")
        except Exception as e:
            logger.error(f"Ertalabki eslatmada xato: {e}")

    async def _resurfaced_block(self) -> str:
        """Brifingga qo'shiladigan eslatmalar bloki (xato bo'lsa bo'sh)."""
        try:
            from app.services.notes import note_service
            notes = await note_service.resurface(active=2, archived=1)
        except Exception as e:
            logger.warning(f"Eslatmalarni qaytarishda xato: {e}")
            return ""
        if not notes:
            return ""

        lines = ["\n\n💡 <b>Ikkinchi miyangizdan:</b>"]
        for n in notes:
            mark = "🔁" if n["tier"] == "archive" else "•"
            lines.append(f"{mark} <b>{n['title']}</b> — {n['summary'][:110]}")
        return "\n".join(lines)

    # ─── time parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_hm(value: str) -> bool:
        h, _, m = value.partition(":")
        return h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60

    @classmethod
    def _parse_time(cls, text: str) -> tuple[str, str | None] | None:
        text = text.strip()
        m = re.match(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", text)
        if m:
            start, end = m.group(1), m.group(2)
            if cls._is_valid_hm(start) and cls._is_valid_hm(end):
                return start, end
            return None
        m = re.match(r"^(\d{1,2}:\d{2})$", text)
        if m:
            return (m.group(1), None) if cls._is_valid_hm(m.group(1)) else None
        m = re.match(r"^(\d{1,2})[.,\s](\d{2})$", text)
        if m:
            candidate = f"{m.group(1)}:{m.group(2)}"
            if cls._is_valid_hm(candidate):
                return candidate, None
            return None
        return None

    # ─── lifecycle ────────────────────────────────────────────────────────────

    async def run_until_disconnected(self) -> None:
        import asyncio
        while True:
            try:
                if self._client:
                    await self._client.run_until_disconnected()
                logger.warning("Bot ulanishi uzildi. Qayta ulanmoqda...")
            except Exception as e:
                logger.error(f"Bot xatosi: {e}. 10 soniyadan keyin qayta ulanadi...")
            await asyncio.sleep(10)
            try:
                if self._client:
                    await self._client.connect()
                    if await self._client.is_user_authorized():
                        logger.info("Bot qayta ulandi")
                        # _register_handlers() qayta CHAQIRILMAYDI —
                        # Telethon handlerlarni saqlab qoladi, duplikat qo'shmaslik uchun.
                    else:
                        logger.error("Bot sessiyasi muddati tugagan")
                        try:
                            # Bot o'zi o'lgani uchun xabarni userbot orqali yuboramiz
                            from app.services.telegram_service import telegram_service
                            await telegram_service.send_message(
                                self._owner_id,
                                "⚠️ Bot sessiyasi muddati tugadi — qayta autentifikatsiya "
                                "kerak. Kunlik reja va post tasdiqlash botlari ishlamayapti.",
                                as_agent=True,
                            )
                        except Exception as notify_err:
                            logger.error(f"Uzilish haqida bildirishnoma yuborilmadi: {notify_err}")
                        break
            except Exception as e:
                logger.error(f"Bot qayta ulanishda xato: {e}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()


bot_service = BotService()

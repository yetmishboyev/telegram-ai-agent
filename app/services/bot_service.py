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
    [Button.inline("🧠 Bilim bazasi (FAQ)", b"faq:menu")],
]

CANCEL_ROW = [[Button.inline("❌ Bekor qilish", b"cancel")]]

FAQ_BUTTONS = [
    [Button.inline("➕ Yangi FAQ qo'shish", b"faq:add")],
    [Button.inline("📋 FAQ ro'yxati", b"faq:list")],
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

        if text in ("/start", "/menu", "/reja", "/plan", "/help"):
            await self._send_main_menu(event.chat_id)
            return

        state = await self._get_state()
        if not state:
            await self._send_main_menu(event.chat_id)
            return

        step = state.get("step")

        if step == "relaying":
            await self._handle_relay(event, state, text)
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

            await self._client.send_message(
                self._owner_id, text, parse_mode="html", buttons=MAIN_BUTTONS
            )
            logger.info("Ertalabki eslatma yuborildi")
        except Exception as e:
            logger.error(f"Ertalabki eslatmada xato: {e}")

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

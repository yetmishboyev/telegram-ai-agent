import asyncio
import re
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.guardrails import check_input, check_output, get_manipulation_reply, get_lang as _detect_lang

from app.ai.agents.analysis_agent import analysis_agent, MessageAnalysis
from app.ai.agents.classifier_agent import classifier_agent, MessageCategory
from app.ai.agents.response_agent import response_agent
from app.ai.agents.style_learner import style_learner
from app.ai.memory.manager import memory_manager
from app.ai.prompts.system_prompt import IMPORTANT_RESPONSES
from app.database.models import TelegramUser, Message, MessageRole, MessageType
from app.database.models import SentimentType, ThreatLevel
from app.database.session import AsyncSessionLocal
from app.repositories.message_repo import message_repo
from app.repositories.user_repo import user_repo
from app.config import settings

# Maxfiy ma'lumot naqshlari — bu turdagi xabarlar Claude API ga YUBORILMAYDI
_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Bank kartasi raqami: 16-19 xonali (bo'sh joyli yoki yo'q) — Luhn tekshiruvi
    # bilan birga qo'llaniladi (pastdagi _detect_sensitive'ga qarang), aks holda
    # istalgan uzun raqam ketma-ketligi (buyurtma raqami va h.k.) bloklanib qolardi.
    ("bank_card", re.compile(r"\b(?:\d[ \-]?){15,18}\d\b")),
    # CVV/CVC: 3-4 xonali, kalit so'z bilan
    ("cvv", re.compile(r"\b(?:cvv|cvc|cvv2|cvc2)\s*[:\-]?\s*\d{3,4}\b", re.I)),
    # PIN kod
    ("pin", re.compile(r"\b(?:pin|пин)\s*[:\-]?\s*\d{4,6}\b", re.I)),
    # Parol
    ("password", re.compile(r"\b(?:password|parol|пароль|парол)\s*[:\-]\s*\S+", re.I)),
    # Passport raqami (O'zbekiston: AA1234567)
    ("passport", re.compile(r"\b[A-Z]{2}\d{7}\b")),
    # JSHSHIR (14 xonali shaxsiy raqam) — kontekst kalit so'zi (jshshir/pinfl)
    # raqamdan oldin kelishi talab qilinadi, aks holda istalgan 14 xonali son
    # (masalan telefon+narsa) bloklanib qolardi.
    ("jshshir", re.compile(r"\b(?:jshshir|jshir|pinfl|шжшир|пинфл)\w*\s*[:\-]?\s*\d{14}\b", re.I)),
    # Foydalanuvchi nomi + parol kombinatsiyasi
    ("credentials", re.compile(r"\b(?:login|username|foydalanuvchi)\s*[:\-]\s*\S+", re.I)),
    # OTP / tasdiqlash kodi
    ("otp", re.compile(r"\b(?:otp|код|code|tasdiqlash kodi)\s*[:\-]?\s*\d{4,8}\b", re.I)),
    # Kriptovalyuta wallet manzili (Bitcoin, Ethereum)
    ("crypto_wallet", re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{6,87})\b")),
]


def _luhn_valid(digits: str) -> bool:
    """Bank kartasi raqami checksumini (Luhn algoritmi) tekshiradi."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0

_SENSITIVE_REPLIES = {
    "uz": (
        "Bu xabar maxfiy ma'lumot (parol, karta raqami yoki shaxsiy hujjat) o'z ichiga olishi mumkin. "
        "Xavfsizlik sababli AI ga yuborilmadi. "
        "Shaxsiy ma'lumotlarni Telegram orqali ulashmang."
    ),
    "ru": (
        "Это сообщение может содержать конфиденциальные данные (пароль, номер карты или личный документ). "
        "В целях безопасности оно не было отправлено AI. "
        "Не передавайте личные данные через Telegram."
    ),
    "en": (
        "This message may contain sensitive information (password, card number, or personal document). "
        "For security reasons, it was not sent to AI. "
        "Please avoid sharing personal data via Telegram."
    ),
}


def _detect_sensitive(text: str) -> str | None:
    """Matnda maxfiy ma'lumot topilsa tur nomini qaytaradi, yo'q bo'lsa None."""
    for name, pattern in _SENSITIVE_PATTERNS:
        if name == "bank_card":
            # Faqat Luhn checksumidan o'tgan ketma-ketlikni karta deb hisoblaymiz —
            # aks holda buyurtma raqami kabi uzun sonlar ham bloklanib qolardi.
            if any(
                _luhn_valid(re.sub(r"[ \-]", "", m.group(0)))
                for m in pattern.finditer(text)
            ):
                return name
            continue
        if pattern.search(text):
            return name
    return None


class AIService:
    """Xabarni qabul qiladi, klassifikatsiya qiladi va javob generatsiya qiladi."""

    async def process_message(
        self,
        db: AsyncSession,
        telegram_id: int,
        text: str,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        message_type: MessageType = MessageType.TEXT,
        telegram_message_id: int | None = None,
    ) -> tuple[Message, str | None]:
        # 1. Foydalanuvchini topish yoki yaratish
        user, is_new = await user_repo.get_or_create(
            db, telegram_id, username, first_name, last_name
        )

        # 2. Blacklist — bloklangan foydalanuvchi statistikaga (total_messages)
        # qo'shilmasligi uchun update_last_seen shu tekshiruvdan KEYIN chaqiriladi.
        if user.is_blacklisted:
            logger.info(f"Blacklisted: {telegram_id}")
            msg = await message_repo.create(
                db=db, user_id=user.id, role=MessageRole.USER,
                content=text, message_type=message_type,
                telegram_message_id=telegram_message_id,
            )
            return msg, None

        await user_repo.update_last_seen(db, user)

        # 2b. Maxfiy ma'lumot filtri — Claude API ga yuborilmaydi
        sensitive_type = _detect_sensitive(text)
        if sensitive_type:
            logger.warning(
                f"Maxfiy ma'lumot aniqlandi [{sensitive_type}]: user={telegram_id} — API ga yuborilmadi"
            )
            msg = await message_repo.create(
                db=db, user_id=user.id, role=MessageRole.USER,
                content="[MAXFIY MA'LUMOT — saqlanmadi]",
                message_type=message_type,
                telegram_message_id=telegram_message_id,
            )
            lang = _detect_lang(text)
            reply = _SENSITIVE_REPLIES.get(lang, _SENSITIVE_REPLIES["uz"])
            msg.agent_response = reply
            await db.flush()
            return msg, reply

        # 2c. Input guardrails — jailbreak, prompt injection, zararli niyat
        input_guard = check_input(text)
        if input_guard.blocked:
            lang = _detect_lang(text)
            reply = (input_guard.reply or {}).get(lang, (input_guard.reply or {}).get("uz", "Xabar bloklandi."))
            msg = await message_repo.create(
                db=db, user_id=user.id, role=MessageRole.USER,
                content=f"[GUARDRAIL:{input_guard.category}]",
                message_type=message_type,
                telegram_message_id=telegram_message_id,
            )
            msg.agent_response = reply
            await db.flush()
            return msg, reply

        # 3. Agent yoqilganmi?
        from app.ai.memory.short_term import short_term_memory
        if not await short_term_memory.is_agent_enabled():
            msg = await message_repo.create(
                db=db, user_id=user.id, role=MessageRole.USER,
                content=text, telegram_message_id=telegram_message_id,
            )
            return msg, None

        # 4. Parallel: spam tahlil + klassifikatsiya + kontekst
        history_task = asyncio.create_task(
            memory_manager.build_context(db, user, text)
        )
        analysis_task = asyncio.create_task(analysis_agent.analyze_message(text))
        classify_task = asyncio.create_task(
            classifier_agent.classify(
                text,
                await short_term_memory.get_recent(telegram_id, n=5),
            )
        )

        analysis: MessageAnalysis
        history: list[dict]
        rag_context: str

        (history, rag_context), analysis, classification = await asyncio.gather(
            history_task, analysis_task, classify_task
        )

        # 5. Xabarni DB ga saqlash
        msg = await message_repo.create(
            db=db,
            user_id=user.id,
            role=MessageRole.USER,
            content=text,
            message_type=message_type,
            telegram_message_id=telegram_message_id,
            sentiment=SentimentType(analysis.sentiment)
                if analysis.sentiment in [e.value for e in SentimentType] else None,
            intent=analysis.intent,
            importance_score=analysis.importance,
            threat_level=ThreatLevel(analysis.threat_level)
                if analysis.threat_level in [e.value for e in ThreatLevel] else ThreatLevel.NONE,
            is_spam=analysis.is_spam,
            confidence_score=analysis.confidence,
        )

        # 6. Spam / fishing / toksik / xavfli xabar filtri — javob berilmaydi
        if (
            analysis.is_spam
            or analysis.is_phishing
            or analysis.is_toxic
            or analysis.threat_level in ("high", "medium")
        ):
            logger.warning(
                f"Filtrlandi: spam={analysis.is_spam}, phishing={analysis.is_phishing}, "
                f"toxic={analysis.is_toxic}, threat={analysis.threat_level}, user={telegram_id}"
            )
            return msg, None

        lang = classification.language

        # 6b. Manipulyatsiya / prompt injection — LLM orqali istalgan tilda aniqlanadi
        # (guardrails.py dagi regex faqat inglizcha, bu qo'shimcha ko'p tilli qatlam)
        if analysis.is_manipulative:
            reply = get_manipulation_reply(lang)
            logger.warning(
                f"[Guardrail/LLM] Manipulyatsiya/prompt injection aniqlandi: user={telegram_id}"
            )
            msg.agent_response = reply
            await db.flush()
            return msg, reply

        # 7. GREETING — jadval bilan dinamik javob
        if classification.category == MessageCategory.GREETING:
            response = await self._build_greeting(lang)
            logger.info(f"GREETING javobi: user={telegram_id}, lang={lang}")
            msg.agent_response = response
            await db.flush()
            await memory_manager.add_exchange(db, user, text, response)
            return msg, response

        # 8. IMPORTANT — jadval bilan dinamik javob + egaga bildirishnoma
        if classification.category == MessageCategory.IMPORTANT or \
                classification.should_notify_owner:
            response = await self._build_important(lang)
            logger.info(
                f"IMPORTANT: user={telegram_id}, sabab={classification.reason}"
            )
            asyncio.create_task(
                self._notify_owner(user, text, classification.reason)
            )
            msg.agent_response = response
            await db.flush()
            await memory_manager.add_exchange(db, user, text, response)
            return msg, response

        # 9. SIMPLE / GENERAL — AI javob generatsiya
        try:
            style_ctx = await style_learner.get_style_context(text)
            schedule_ctx = await self._get_schedule_context()

            agent_response = await response_agent.generate_response(
                user=user,
                current_message=text,
                history=history,
                rag_context=rag_context,
                relationship_type=user.relationship_type,
                conversation_summary=await memory_manager.get_latest_summary(
                    db, user.id
                ),
                schedule_context=schedule_ctx,
                style_examples=style_ctx,
            )
        except Exception as e:
            logger.error(f"Javob generatsiyada xato: {e}")
            return msg, None

        # 9b. Output guardrails — AI javobi xavfli kontent yoki system leak bo'lmasin
        output_guard = check_output(agent_response)
        if output_guard.blocked:
            from app.ai.guardrails import OUTPUT_FALLBACK
            detected_lang = _detect_lang(text)
            fallback = OUTPUT_FALLBACK.get(detected_lang, OUTPUT_FALLBACK["uz"])
            msg.agent_response = fallback
            await db.flush()
            logger.warning(
                f"[Guardrail/output] Javob almashtirildi: category={output_guard.category}, user={telegram_id}"
            )
            return msg, fallback

        # 10. Ishonch past → egaga bildirishnoma + tayyor javob
        if analysis.confidence < settings.confidence_threshold:
            logger.info(
                f"Ishonch past ({analysis.confidence:.2f}) — egaga yo'naltirildi"
            )
            asyncio.create_task(
                self._notify_owner(
                    user, text,
                    f"Ishonch past ({analysis.confidence:.2f}) — AI javob bera olmadi"
                )
            )
            fallback = IMPORTANT_RESPONSES.get(lang, IMPORTANT_RESPONSES["uz"])
            msg.agent_response = fallback
            await db.flush()
            await memory_manager.add_exchange(db, user, text, fallback)
            return msg, fallback

        msg.agent_response = agent_response
        await db.flush()

        # 11. Faktlarni ajratish + xotirani yangilash (background)
        asyncio.create_task(self._extract_and_save_facts(user.id, text))
        await memory_manager.add_exchange(db, user, text, agent_response)

        return msg, agent_response

    async def _build_greeting(self, lang: str) -> str:
        try:
            from app.repositories.task_repo import get_current_status
            status = await get_current_status(lang)
        except Exception:
            status = None

        if lang == "ru":
            intro = "Я AI-агент Шахзодбека Йетмишбоева."
            ending = "Чем могу помочь?"
        elif lang == "en":
            intro = "I'm Shaxzodbek Yetmishboyev's AI agent."
            ending = "How can I help you?"
        else:
            intro = "Men Shaxzodbek Yetmishboyevning AI agentiman."
            ending = "Sizga qanday yordam bera olaman?"

        if status:
            return f"{intro} {status.capitalize()}. {ending}"
        return f"{intro} {ending}"

    async def _build_important(self, lang: str) -> str:
        try:
            from app.repositories.task_repo import get_current_status
            status = await get_current_status(lang)
        except Exception:
            status = None

        if lang == "ru":
            intro = "Я AI-агент Шахзодбека Йетмишбоева."
            ending = "Пожалуйста, оставьте ваш вопрос — Шахзодбек ответит как можно скорее."
        elif lang == "en":
            intro = "I'm Shaxzodbek Yetmishboyev's AI agent."
            ending = "Please leave your message — Shaxzodbek will respond as soon as possible."
        else:
            intro = "Men Shaxzodbek Yetmishboyevning AI agentiman."
            ending = "Iltimos, savolingizni yozib qoldiring — Shaxzodbek imkon topgach javob beradi."

        if status:
            return f"{intro} {status.capitalize()}. {ending}"
        return f"{intro} {ending}"

    async def _notify_owner(
        self, user: TelegramUser, text: str, reason: str
    ) -> None:
        try:
            from app.services.notification_service import notification_service
            await notification_service.notify_important(user, text, reason)
        except Exception as e:
            logger.error(f"Bildirishnomada xato: {e}")

    async def _get_schedule_context(self) -> str:
        try:
            from app.repositories.task_repo import get_schedule_context
            return await get_schedule_context()
        except Exception:
            return ""

    async def _extract_and_save_facts(self, user_id: int, message: str) -> None:
        # Alohida (yangi) sessiyada ishlaydi — chaqiruvchining so'rov-doirasidagi
        # `db` sessiyasi fon vazifasi tugaguncha allaqachon yopilgan bo'lishi mumkin.
        try:
            facts = await analysis_agent.extract_facts(message)
            if not facts:
                return
            async with AsyncSessionLocal() as db:
                user = await db.get(TelegramUser, user_id)
                if user is None:
                    return
                await memory_manager.save_extracted_facts(db, user, facts)
                await db.commit()
        except Exception as e:
            logger.warning(f"Fakt saqlashda xato: {e}")

    async def mark_sent(self, db: AsyncSession, message: Message) -> None:
        message.was_sent = True
        await db.flush()

    async def apply_admin_override(
        self, db: AsyncSession, message: Message, override_text: str
    ) -> None:
        message.admin_override = override_text
        message.was_approved = True
        await db.flush()


ai_service = AIService()

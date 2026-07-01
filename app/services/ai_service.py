import asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.analysis_agent import analysis_agent, MessageAnalysis
from app.ai.agents.classifier_agent import classifier_agent, MessageCategory
from app.ai.agents.response_agent import response_agent
from app.ai.agents.style_learner import style_learner
from app.ai.memory.manager import memory_manager
from app.ai.prompts.system_prompt import GREETING_RESPONSES, IMPORTANT_RESPONSES
from app.database.models import TelegramUser, Message, MessageRole, MessageType
from app.database.models import SentimentType, ThreatLevel
from app.repositories.message_repo import message_repo
from app.repositories.user_repo import user_repo
from app.config import settings


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
        await user_repo.update_last_seen(db, user)

        # 2. Blacklist
        if user.is_blacklisted:
            logger.info(f"Blacklisted: {telegram_id}")
            msg = await message_repo.create(
                db=db, user_id=user.id, role=MessageRole.USER,
                content=text, message_type=message_type,
                telegram_message_id=telegram_message_id,
            )
            return msg, None

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

        # 6. Spam / xavfli xabar filtri
        if analysis.is_spam or analysis.threat_level in ("high", "medium"):
            logger.warning(
                f"Filtrlandi: spam={analysis.is_spam}, threat={analysis.threat_level}, "
                f"user={telegram_id}"
            )
            return msg, None

        lang = classification.language

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
        asyncio.create_task(self._extract_and_save_facts(db, user, text))
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

    async def _extract_and_save_facts(
        self, db: AsyncSession, user: TelegramUser, message: str
    ) -> None:
        try:
            facts = await analysis_agent.extract_facts(message)
            if facts:
                await memory_manager.save_extracted_facts(db, user, facts)
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

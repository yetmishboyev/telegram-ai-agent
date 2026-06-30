import asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.analysis_agent import analysis_agent, MessageAnalysis
from app.ai.agents.response_agent import response_agent
from app.ai.memory.manager import memory_manager
from app.database.models import TelegramUser, Message, MessageRole, MessageType
from app.database.models import SentimentType, ThreatLevel
from app.repositories.message_repo import message_repo
from app.repositories.user_repo import user_repo
from app.config import settings


class AIService:
    """Xabarni qabul qiladi, tahlil qiladi, javob generatsiya qiladi."""

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
        """
        Xabarni to'liq qayta ishlaydi.

        Returns:
            tuple: (saqlangan xabar, agent javobi yoki None)
        """
        # 1. Foydalanuvchini topish yoki yaratish
        user, is_new = await user_repo.get_or_create(
            db, telegram_id, username, first_name, last_name
        )
        await user_repo.update_last_seen(db, user)

        # 2. Blacklist tekshirish
        if user.is_blacklisted:
            logger.info(f"Blacklisted foydalanuvchi o'tkazib yuborildi: {telegram_id}")
            msg = await message_repo.create(
                db=db,
                user_id=user.id,
                role=MessageRole.USER,
                content=text,
                message_type=message_type,
                telegram_message_id=telegram_message_id,
            )
            return msg, None

        # 3. Agent yoqilganmi?
        from app.ai.memory.short_term import short_term_memory
        if not await short_term_memory.is_agent_enabled():
            msg = await message_repo.create(
                db=db,
                user_id=user.id,
                role=MessageRole.USER,
                content=text,
                telegram_message_id=telegram_message_id,
            )
            return msg, None

        # 4. Parallel: tahlil + kontekst tayyorlash
        analysis_task = asyncio.create_task(analysis_agent.analyze_message(text))
        context_task = asyncio.create_task(
            memory_manager.build_context(db, user, text)
        )

        analysis: MessageAnalysis
        history: list[dict]
        rag_context: str

        analysis, (history, rag_context) = await asyncio.gather(
            analysis_task, context_task
        )

        # 5. Xabarni DB ga saqlash
        msg = await message_repo.create(
            db=db,
            user_id=user.id,
            role=MessageRole.USER,
            content=text,
            message_type=message_type,
            telegram_message_id=telegram_message_id,
            sentiment=SentimentType(analysis.sentiment) if analysis.sentiment in [e.value for e in SentimentType] else None,
            intent=analysis.intent,
            importance_score=analysis.importance,
            threat_level=ThreatLevel(analysis.threat_level) if analysis.threat_level in [e.value for e in ThreatLevel] else ThreatLevel.NONE,
            is_spam=analysis.is_spam,
            confidence_score=analysis.confidence,
        )

        # 6. Spam / xavfli xabar filtratsiyasi
        if analysis.is_spam or analysis.threat_level in ("high", "medium"):
            logger.warning(
                f"Xabar filtrlandi: spam={analysis.is_spam}, "
                f"threat={analysis.threat_level}, user={telegram_id}"
            )
            return msg, None

        if not analysis.should_respond:
            logger.info(f"Javob bermaslik qaroriga kelindi: {analysis.reason}")
            return msg, None

        if analysis.confidence < settings.confidence_threshold:
            logger.info(f"Ishonch past: {analysis.confidence}, o'tkazildi")
            return msg, None

        # 7. Javob generatsiya
        try:
            agent_response = await response_agent.generate_response(
                user=user,
                current_message=text,
                history=history,
                rag_context=rag_context,
                relationship_type=user.relationship_type,
                conversation_summary=await memory_manager.get_latest_summary(db, user.id),
            )
        except Exception as e:
            logger.error(f"Javob generatsiyada xato: {e}")
            return msg, None

        msg.agent_response = agent_response
        await db.flush()

        # 8. Faktlarni ajratib xotiraga saqlash (background)
        asyncio.create_task(self._extract_and_save_facts(db, user, text))

        # 9. Xotirani yangilash
        await memory_manager.add_exchange(db, user, text, agent_response)

        return msg, agent_response

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

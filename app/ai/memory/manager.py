import uuid as uuid_lib
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.ai.memory.short_term import short_term_memory
from app.ai.memory.long_term import long_term_memory
from app.ai.rag.retriever import rag_retriever
from app.ai.rag.indexer import rag_indexer
from app.database.models import ConversationSummary, TelegramUser
from app.config import settings


class MemoryManager:
    """Barcha xotira qatlamlarini boshqaradi."""

    async def add_exchange(
        self,
        db: AsyncSession,
        user: TelegramUser,
        user_message: str,
        agent_response: str,
    ) -> None:
        """Foydalanuvchi xabari va agent javobini barcha qatlamlarga yozadi."""
        await short_term_memory.add_message(user.telegram_id, "user", user_message)
        await short_term_memory.add_message(user.telegram_id, "assistant", agent_response)

        count = await short_term_memory.count(user.telegram_id)
        if count >= settings.agent_summary_threshold:
            await self._summarize_and_compress(db, user)

    async def build_context(
        self,
        db: AsyncSession,
        user: TelegramUser,
        current_message: str,
    ) -> tuple[list[dict], str]:
        """Agent uchun to'liq kontekst tayyorlaydi.

        Returns:
            tuple: (chat_history, rag_context_string)
        """
        history = await short_term_memory.get_history(user.telegram_id)

        # RAG: Semantic search
        rag_items = await rag_retriever.retrieve(
            query=current_message,
            user_id=user.id,
            n_results=5,
        )
        rag_context = rag_retriever.format_context(rag_items)

        # Xulosa (agar bor bo'lsa)
        if not history:
            summary = await self._get_latest_summary(db, user.id)
            if summary:
                rag_context = f"## Oldingi suhbat xulosasi:\n{summary.summary}\n\n{rag_context}"

        return history, rag_context

    async def get_latest_summary(self, db: AsyncSession, user_id: int) -> str | None:
        summary = await self._get_latest_summary(db, user_id)
        return summary.summary if summary else None

    async def _get_latest_summary(
        self, db: AsyncSession, user_id: int
    ) -> ConversationSummary | None:
        result = await db.execute(
            select(ConversationSummary)
            .where(ConversationSummary.user_id == user_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _summarize_and_compress(
        self, db: AsyncSession, user: TelegramUser
    ) -> None:
        """Uzoq suhbatlarni xulosalayd va qisqartiradi."""
        from app.ai.agents.response_agent import response_agent

        history = await short_term_memory.get_history(user.telegram_id)
        if not history:
            return

        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history
        )

        try:
            summary_text = await response_agent.summarize(history_text)
        except Exception as e:
            logger.error(f"Xulosa yaratishda xato: {e}")
            return

        summary_id = str(uuid_lib.uuid4())
        summary = ConversationSummary(
            user_id=user.id,
            summary=summary_text,
            message_count=len(history),
        )
        db.add(summary)
        await db.flush()

        try:
            await rag_indexer.index_summary(summary_id, summary_text, user.id)
            summary.vector_id = summary_id
        except Exception as e:
            logger.warning(f"Xulosa vektorlashda xato: {e}")

        await short_term_memory.clear(user.telegram_id)
        logger.info(f"Suhbat {len(history)} ta xabar xulosalandi: user={user.telegram_id}")

    async def save_extracted_facts(
        self, db: AsyncSession, user: TelegramUser, facts: list[dict]
    ) -> None:
        await long_term_memory.update_user_profile(db, user, facts)

    async def clear_user(self, db: AsyncSession, user: TelegramUser) -> None:
        await short_term_memory.clear(user.telegram_id)
        await long_term_memory.delete_user_memory(db, user.id)


memory_manager = MemoryManager()

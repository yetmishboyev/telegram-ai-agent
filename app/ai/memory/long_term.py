import uuid as uuid_lib
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.ai.guardrails import check_input
from app.database.models import MemoryEntry, TelegramUser
from app.ai.rag.indexer import rag_indexer

_MAX_FACT_VALUE_LEN = 200


def _sanitize_fact_value(value: str) -> str:
    """Ajratilgan fakt qiymatini saqlashdan oldin tozalaydi.

    Bu qiymat keyinchalik RAG orqali system promptga qaytishi mumkin
    (ikkinchi-tartibli prompt-injection xavfi) — shuning uchun oddiy
    foydalanuvchi xabari uchun ishlatiladigan guardrail bilan tekshiramiz
    va uzunlikni cheklaymiz.
    """
    value = value.strip().replace("\n", " ").replace("\r", " ")
    if not value:
        return ""
    if check_input(value).blocked:
        logger.warning(f"Fakt qiymati bloklandi (guardrail): {value[:60]!r}")
        return ""
    return value[:_MAX_FACT_VALUE_LEN]


class LongTermMemory:
    """PostgreSQL + ChromaDB asosida uzoq muddatli xotira."""

    async def save_fact(
        self,
        db: AsyncSession,
        user_id: int,
        category: str,
        key: str,
        value: str,
        importance: float = 0.5,
        source_message_id: int | None = None,
    ) -> MemoryEntry:
        # Mavjud yozuvni yangilash yoki yangisini yaratish
        result = await db.execute(
            select(MemoryEntry).where(
                MemoryEntry.user_id == user_id,
                MemoryEntry.category == category,
                MemoryEntry.key == key,
            )
        )
        entry = result.scalar_one_or_none()

        if entry:
            entry.value = value
            entry.importance = importance
        else:
            entry = MemoryEntry(
                uuid=str(uuid_lib.uuid4()),
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                importance=importance,
                source_message_id=source_message_id,
            )
            db.add(entry)

        await db.flush()

        # Vektor bazasiga indekslash
        try:
            vector_id = await rag_indexer.index_memory(entry, user_id)
            entry.vector_id = vector_id
        except Exception as e:
            logger.warning(f"Vektorlash muvaffaqiyatsiz: {e}")

        logger.debug(f"Xotira saqlandi: user={user_id}, {category}/{key}")
        return entry

    async def get_facts(
        self,
        db: AsyncSession,
        user_id: int,
        category: str | None = None,
    ) -> list[MemoryEntry]:
        query = select(MemoryEntry).where(MemoryEntry.user_id == user_id)
        if category:
            query = query.where(MemoryEntry.category == category)
        query = query.order_by(MemoryEntry.importance.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def update_user_profile(
        self, db: AsyncSession, user: TelegramUser, extracted_facts: list[dict]
    ) -> None:
        """AI tomonidan ajratilgan faktlarni profilga va xotiraga yozadi."""
        for fact in extracted_facts:
            category = fact.get("category", "fact")
            key = fact.get("key", "")
            value = _sanitize_fact_value(str(fact.get("value", "")))
            importance = float(fact.get("importance", 0.5))

            if not key or not value:
                continue

            await self.save_fact(
                db=db,
                user_id=user.id,
                category=category,
                key=key,
                value=value,
                importance=importance,
            )

            # Maxsus kategoriyalarni to'g'ridan-to'g'ri profilga ham yozamiz
            if key == "kasbi" or key == "profession":
                user.profession = value
            elif key == "kompaniya" or key == "company":
                user.company = value

        logger.info(f"Profil yangilandi: user={user.id}, {len(extracted_facts)} ta fakt")

    async def delete_user_memory(self, db: AsyncSession, user_id: int) -> None:
        entries = await self.get_facts(db, user_id)
        for entry in entries:
            await db.delete(entry)
        await rag_indexer.remove_user_data(user_id)
        logger.info(f"Foydalanuvchi {user_id} xotirasi o'chirildi")


long_term_memory = LongTermMemory()

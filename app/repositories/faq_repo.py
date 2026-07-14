from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FaqEntry


async def create_faq(
    db: AsyncSession, question: str, answer: str
) -> FaqEntry:
    entry = FaqEntry(question=question, answer=answer)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_faqs(db: AsyncSession, active_only: bool = True) -> list[FaqEntry]:
    stmt = select(FaqEntry).order_by(FaqEntry.created_at.desc())
    if active_only:
        stmt = stmt.where(FaqEntry.is_active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_faq(db: AsyncSession, faq_id: int) -> FaqEntry | None:
    result = await db.execute(select(FaqEntry).where(FaqEntry.id == faq_id))
    return result.scalar_one_or_none()


async def set_vector_id(db: AsyncSession, faq_id: int, vector_id: str) -> None:
    entry = await get_faq(db, faq_id)
    if entry:
        entry.vector_id = vector_id
        await db.commit()


async def delete_faq(db: AsyncSession, faq_id: int) -> FaqEntry | None:
    """FAQ ni o'chiradi va (chaqiruvchi vektorni ham tozalashi uchun) yozuvni qaytaradi."""
    entry = await get_faq(db, faq_id)
    if entry:
        await db.delete(entry)
        await db.commit()
    return entry

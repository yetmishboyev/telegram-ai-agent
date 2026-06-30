from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database.session import get_db
from app.database.models import AdminUser, TelegramUser, MemoryEntry, ConversationSummary
from app.api.dependencies import get_current_admin

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryEntryOut(BaseModel):
    id: int
    category: str
    key: str
    value: str
    importance: float
    created_at: str


@router.get("/{telegram_id}")
async def get_user_memory(
    telegram_id: int,
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    query = select(MemoryEntry).where(MemoryEntry.user_id == user.id)
    if category:
        query = query.where(MemoryEntry.category == category)
    query = query.order_by(MemoryEntry.importance.desc())

    result2 = await db.execute(query)
    entries = result2.scalars().all()

    # Xulosalar
    result3 = await db.execute(
        select(ConversationSummary)
        .where(ConversationSummary.user_id == user.id)
        .order_by(ConversationSummary.created_at.desc())
        .limit(5)
    )
    summaries = result3.scalars().all()

    return {
        "user": user.display_name,
        "facts": [
            {
                "id": e.id,
                "category": e.category,
                "key": e.key,
                "value": e.value,
                "importance": e.importance,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
        "summaries": [
            {
                "summary": s.summary,
                "message_count": s.message_count,
                "created_at": s.created_at.isoformat(),
            }
            for s in summaries
        ],
    }


@router.delete("/{telegram_id}/fact/{fact_id}")
async def delete_fact(
    telegram_id: int,
    fact_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(select(MemoryEntry).where(MemoryEntry.id == fact_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Fakt topilmadi")
    await db.delete(entry)
    await db.commit()
    return {"ok": True}

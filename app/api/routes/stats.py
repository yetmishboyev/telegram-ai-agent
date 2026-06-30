from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_db
from app.database.models import AdminUser, TelegramUser, Message, AgentLog
from app.api.dependencies import get_current_admin
from app.repositories.message_repo import message_repo
from app.repositories.user_repo import user_repo

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    total_users = await user_repo.count(db)
    total_messages = await message_repo.total_count(db)
    sent_today = await message_repo.count_sent_today(db)

    result = await db.execute(
        select(func.count(Message.id)).where(Message.is_spam == True)
    )
    spam_blocked = result.scalar() or 0

    return {
        "total_users": total_users,
        "total_messages": total_messages,
        "sent_today": sent_today,
        "spam_blocked": spam_blocked,
    }


@router.get("/logs")
async def get_logs(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(
        select(AgentLog).order_by(AgentLog.created_at.desc()).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": l.id,
            "level": l.level,
            "component": l.component,
            "message": l.message,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ]

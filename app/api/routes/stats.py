from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_db
from app.database.models import AdminUser, TelegramUser, Message, AgentLog, MessageRole
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

    approved_result = await db.execute(
        select(func.count(Message.id)).where(Message.was_approved == True)
    )
    total_approved = approved_result.scalar() or 0

    edited_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.was_approved == True,
            Message.admin_override.isnot(None),
        )
    )
    total_edited = edited_result.scalar() or 0

    approval_rate_unedited = (
        round((total_approved - total_edited) / total_approved * 100, 1)
        if total_approved > 0
        else None
    )

    return {
        "total_users": total_users,
        "total_messages": total_messages,
        "sent_today": sent_today,
        "spam_blocked": spam_blocked,
        "total_approved": total_approved,
        "total_edited": total_edited,
        "approval_rate_unedited": approval_rate_unedited,
    }


@router.get("/telegram")
async def get_telegram_status(_: AdminUser = Depends(get_current_admin)):
    from app.services.telegram_service import telegram_service
    try:
        connected = telegram_service._client.is_connected()
        user = telegram_service._me.first_name if telegram_service._me else None
    except Exception:
        connected = False
        user = None
    return {"connected": connected, "user": user}


@router.get("/weekly")
async def get_weekly_stats(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    uz_days = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        r = await db.execute(
            select(func.count(Message.id)).where(
                Message.created_at >= start,
                Message.created_at < end,
                Message.role == MessageRole.USER,
            )
        )
        result.append({
            "label": uz_days[day.weekday()],
            "date": day.isoformat(),
            "count": r.scalar() or 0,
        })
    return result


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

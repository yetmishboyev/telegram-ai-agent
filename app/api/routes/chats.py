import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database.session import get_db
from app.database.models import AdminUser, Message
from app.api.dependencies import get_current_admin
from app.ai.agents.style_learner import style_learner
from app.repositories.user_repo import user_repo
from app.repositories.message_repo import message_repo
from app.services.telegram_service import telegram_service

router = APIRouter(prefix="/chats", tags=["chats"])


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    agent_response: str | None
    was_sent: bool
    was_approved: bool | None
    created_at: str

    class Config:
        from_attributes = True


class SendMessageIn(BaseModel):
    telegram_id: int
    text: str


class ApproveIn(BaseModel):
    message_id: int
    text: str | None = None


@router.get("/")
async def list_users(
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    users = await user_repo.get_all(db, limit=limit, offset=offset)
    total = await user_repo.count(db)
    return {
        "total": total,
        "users": [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "name": u.display_name,
                "username": u.username,
                "total_messages": u.total_messages,
                "last_seen": u.last_seen.isoformat() if u.last_seen else None,
                "is_blacklisted": u.is_blacklisted,
            }
            for u in users
        ],
    }


@router.get("/{telegram_id}/messages")
async def get_chat_messages(
    telegram_id: int,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    user = await user_repo.get_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    messages = await message_repo.get_by_user(db, user.id, limit=limit, offset=offset)
    return {
        "user": {"name": user.display_name, "telegram_id": telegram_id},
        "messages": [
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "agent_response": m.agent_response,
                "was_sent": m.was_sent,
                "was_approved": m.was_approved,
                "sentiment": m.sentiment.value if m.sentiment else None,
                "intent": m.intent,
                "importance": m.importance_score,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("/{telegram_id}/blacklist")
async def blacklist_user(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Foydalanuvchini blacklistga qo'shish."""
    from app.database.models import TelegramUser
    from sqlalchemy import select
    result = await db.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    user.is_blacklisted = True
    await db.commit()
    return {"ok": True, "message": f"{user.display_name} blacklistga qo'shildi"}


@router.delete("/{telegram_id}/blacklist")
async def unblacklist_user(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Foydalanuvchini blacklistdan chiqarish."""
    from app.database.models import TelegramUser
    from sqlalchemy import select
    result = await db.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    user.is_blacklisted = False
    await db.commit()
    return {"ok": True, "message": f"{user.display_name} blacklistdan chiqarildi"}


@router.post("/send")
async def send_manual_message(
    body: SendMessageIn,
    _: AdminUser = Depends(get_current_admin),
):
    """Admin tomonidan to'g'ridan-to'g'ri xabar yuborish."""
    try:
        await telegram_service.send_message(body.telegram_id, body.text)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve")
async def approve_response(
    body: ApproveIn,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Agent javobini tasdiqlash va yuborish."""
    from sqlalchemy import select
    result = await db.execute(select(Message).where(Message.id == body.message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Xabar topilmadi")

    final_text = body.text or msg.agent_response
    if not final_text:
        raise HTTPException(status_code=400, detail="Yuborish uchun matn yo'q")

    # user_id bu erda TelegramUser.id — telegram_id emas
    from sqlalchemy import select as sel
    from app.database.models import TelegramUser
    result2 = await db.execute(sel(TelegramUser).where(TelegramUser.id == msg.user_id))
    tg_user = result2.scalar_one_or_none()

    if tg_user:
        await telegram_service.send_message(tg_user.telegram_id, final_text)

    msg.was_sent = True
    msg.was_approved = True
    if body.text:
        msg.admin_override = body.text
        # Eganing tuzatgan yakuniy matni — eng qimmatli uslub signali (fon vazifasi)
        asyncio.create_task(style_learner.learn(body.text))
    await db.commit()
    return {"ok": True}

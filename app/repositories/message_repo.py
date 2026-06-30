from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database.models import Message, MessageRole


class MessageRepository:
    async def create(
        self,
        db: AsyncSession,
        user_id: int,
        role: MessageRole,
        content: str,
        **kwargs,
    ) -> Message:
        message = Message(user_id=user_id, role=role, content=content, **kwargs)
        db.add(message)
        await db.flush()
        return message

    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Message]:
        result = await db.execute(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_pending_approval(
        self, db: AsyncSession, limit: int = 20
    ) -> list[Message]:
        result = await db.execute(
            select(Message)
            .where(
                Message.agent_response.is_not(None),
                Message.was_approved.is_(None),
                Message.was_sent == False,
            )
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_user(self, db: AsyncSession, user_id: int) -> int:
        result = await db.execute(
            select(func.count(Message.id)).where(Message.user_id == user_id)
        )
        return result.scalar() or 0

    async def total_count(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count(Message.id)))
        return result.scalar() or 0

    async def count_sent_today(self, db: AsyncSession) -> int:
        from datetime import datetime, timezone
        from sqlalchemy import cast, Date
        today = datetime.now(timezone.utc).date()
        result = await db.execute(
            select(func.count(Message.id)).where(
                Message.was_sent == True,
                cast(Message.created_at, Date) == today,
            )
        )
        return result.scalar() or 0


message_repo = MessageRepository()

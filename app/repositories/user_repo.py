from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database.models import TelegramUser


class UserRepository:
    async def get_by_telegram_id(
        self, db: AsyncSession, telegram_id: int
    ) -> TelegramUser | None:
        result = await db.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        db: AsyncSession,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[TelegramUser, bool]:
        user = await self.get_by_telegram_id(db, telegram_id)
        if user:
            return user, False

        user = TelegramUser(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        db.add(user)
        await db.flush()
        return user, True

    async def update_last_seen(self, db: AsyncSession, user: TelegramUser) -> None:
        from datetime import datetime, timezone
        user.last_seen = datetime.now(timezone.utc)
        user.total_messages += 1

    async def set_blacklisted(
        self, db: AsyncSession, telegram_id: int, blacklisted: bool
    ) -> None:
        await db.execute(
            update(TelegramUser)
            .where(TelegramUser.telegram_id == telegram_id)
            .values(is_blacklisted=blacklisted)
        )


    async def get_all(
        self, db: AsyncSession, limit: int = 100, offset: int = 0
    ) -> list[TelegramUser]:
        result = await db.execute(
            select(TelegramUser)
            .order_by(TelegramUser.last_seen.desc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count(self, db: AsyncSession) -> int:
        from sqlalchemy import func
        result = await db.execute(select(func.count(TelegramUser.id)))
        return result.scalar() or 0


user_repo = UserRepository()

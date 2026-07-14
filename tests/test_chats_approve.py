"""`/api/chats/approve` — eganing tahriri style_learner'ga o'rgatilishini
tekshiradi (roadmap Faza 3, band 8).

Eslatma: `/approve` route o'zi `db.commit()` chaqiradi, shuning uchun bu
yerda yaratilgan qatorlar `db_session` fixture'ning odatiy rollback bilan
tozalanmaydi — har bir testda yaratilgan qatorlarni `finally`da qo'lda
o'chiramiz, aks holda qayta ishga tushirilganda telegram_id unique
constraint xatosi beradi.
"""
import asyncio
import random
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete
from unittest.mock import AsyncMock, patch

from app.database.models import AdminUser, TelegramUser, Message, MessageRole


def _random_telegram_id() -> int:
    return random.randint(900_000_000, 999_999_999)


@pytest.mark.asyncio
async def test_approve_with_edit_teaches_style_learner(db_session):
    from app.main import app
    from app.database.session import get_db
    from app.api.dependencies import get_current_admin

    user = TelegramUser(telegram_id=_random_telegram_id(), first_name="Test")
    db_session.add(user)
    await db_session.flush()

    msg = Message(
        user_id=user.id,
        role=MessageRole.USER,
        content="Salom",
        agent_response="Salom, yaxshiman!",
    )
    db_session.add(msg)
    await db_session.flush()
    user_id, msg_id = user.id, msg.id

    admin = AdminUser(username="test_admin_approve", hashed_password="x", is_active=True)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_admin] = lambda: admin
    try:
        with patch("app.api.routes.chats.telegram_service.send_message", AsyncMock()), \
             patch("app.api.routes.chats.style_learner.learn", AsyncMock()) as mock_learn:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/chats/approve",
                    json={"message_id": msg_id, "text": "Tahrirlangan yakuniy matn"},
                )
            assert resp.status_code == 200
            await asyncio.sleep(0.05)  # fire-and-forget create_task tugashini kutish
            mock_learn.assert_called_once_with("Tahrirlangan yakuniy matn")
    finally:
        app.dependency_overrides.clear()
        await db_session.execute(delete(Message).where(Message.id == msg_id))
        await db_session.execute(delete(TelegramUser).where(TelegramUser.id == user_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_approve_without_edit_does_not_teach_style_learner(db_session):
    from app.main import app
    from app.database.session import get_db
    from app.api.dependencies import get_current_admin

    user = TelegramUser(telegram_id=_random_telegram_id(), first_name="Test2")
    db_session.add(user)
    await db_session.flush()

    msg = Message(
        user_id=user.id,
        role=MessageRole.USER,
        content="Salom",
        agent_response="Salom, yaxshiman!",
    )
    db_session.add(msg)
    await db_session.flush()
    user_id, msg_id = user.id, msg.id

    admin = AdminUser(username="test_admin_approve2", hashed_password="x", is_active=True)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_admin] = lambda: admin
    try:
        with patch("app.api.routes.chats.telegram_service.send_message", AsyncMock()), \
             patch("app.api.routes.chats.style_learner.learn", AsyncMock()) as mock_learn:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/chats/approve",
                    json={"message_id": msg_id},
                )
            assert resp.status_code == 200
            await asyncio.sleep(0.05)
            mock_learn.assert_not_called()
    finally:
        app.dependency_overrides.clear()
        await db_session.execute(delete(Message).where(Message.id == msg_id))
        await db_session.execute(delete(TelegramUser).where(TelegramUser.id == user_id))
        await db_session.commit()

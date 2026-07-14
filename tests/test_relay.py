"""Ikki tomonlama relay testlari — eganing javobi userbot orqali yetkaziladi."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.bot_service import bot_service


@pytest.mark.asyncio
async def test_relay_reply_sends_via_userbot():
    with patch(
        "app.services.telegram_service.telegram_service.send_message", AsyncMock()
    ) as mock_send:
        ok = await bot_service.relay_reply(123456, "Salom, mana javobim")

    assert ok is True
    mock_send.assert_awaited_once_with(123456, "Salom, mana javobim")


@pytest.mark.asyncio
async def test_relay_reply_returns_false_on_error():
    with patch(
        "app.services.telegram_service.telegram_service.send_message",
        AsyncMock(side_effect=RuntimeError("entity topilmadi")),
    ):
        ok = await bot_service.relay_reply(123456, "test")

    assert ok is False


@pytest.mark.asyncio
async def test_lookup_user_name_returns_display_name(db_session):
    from app.database.models import TelegramUser

    user = TelegramUser(telegram_id=900020001, first_name="Dilnoza", last_name="K")
    db_session.add(user)
    await db_session.commit()
    try:
        name = await bot_service._lookup_user_name(900020001)
        assert name == "Dilnoza K"
    finally:
        await db_session.delete(user)
        await db_session.commit()


@pytest.mark.asyncio
async def test_lookup_user_name_falls_back_to_id():
    name = await bot_service._lookup_user_name(999999999)
    assert name == "999999999"

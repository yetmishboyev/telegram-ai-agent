"""Agentning O'Z javoblari "ega yozdi" deb hisoblanmasligi kerak.

Userbot yuborgan javob chiquvchi xabar sifatida qaytib keladi. Himoyasiz
holda bu ikki zarar berardi:
  1. `owner_active` belgisi qo'yilib, agent o'z javobidan keyin o'sha chatda
     10 daqiqa jim qolardi;
  2. agent o'z matnini eganing uslub namunasi sifatida o'rganib, uslub
     asta-sekin o'ziga qarab siljib ketardi.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.telegram_service import telegram_service


@pytest.fixture(autouse=True)
def _clean_state():
    """Servis singleton — testlar orasida holatni tozalab turamiz."""
    telegram_service._agent_sent_ids.clear()
    telegram_service._sending_chats.clear()
    yield
    telegram_service._agent_sent_ids.clear()
    telegram_service._sending_chats.clear()


def _event(chat_id: int, message_id: int, text: str = "matn"):
    return SimpleNamespace(
        chat_id=chat_id,
        message=SimpleNamespace(id=message_id, text=text),
    )


def test_owner_message_is_not_treated_as_agent_message():
    assert telegram_service._is_agent_message(_event(111, 1)) is False


@pytest.mark.asyncio
async def test_sent_message_id_is_remembered():
    with patch.object(telegram_service, "_client") as client:
        client.send_message = AsyncMock(return_value=SimpleNamespace(id=4242))
        await telegram_service._send_as_agent(111, "javob matni")

    # Chat oynasi emas, aynan id bo'yicha tanilishini tekshiramiz
    telegram_service._sending_chats.discard(111)
    assert telegram_service._is_agent_message(_event(111, 4242)) is True
    # Boshqa xabar (ega qo'lda yozgani) tegilmaydi
    assert telegram_service._is_agent_message(_event(111, 9999)) is False


@pytest.mark.asyncio
async def test_chat_is_guarded_while_sending():
    """Update xabar id si ma'lum bo'lishidan oldin ham kelishi mumkin."""
    seen: dict = {}

    async def slow_send(chat_id, text, reply_to=None):
        # Yuborish davom etayotgan payt: id hali yo'q, lekin chat himoyalangan
        seen["guarded"] = telegram_service._is_agent_message(_event(chat_id, 777))
        return SimpleNamespace(id=777)

    with patch.object(telegram_service, "_client") as client:
        client.send_message = AsyncMock(side_effect=slow_send)
        await telegram_service._send_as_agent(222, "javob")

    assert seen["guarded"] is True


@pytest.mark.asyncio
async def test_send_message_marks_agent_text_only_when_asked():
    """Relay (ega yozgan matn) himoyalanmaydi — u haqiqatan eganing xabari."""
    with patch.object(telegram_service, "_client") as client:
        client.send_message = AsyncMock(return_value=SimpleNamespace(id=10))
        await telegram_service.send_message(333, "ega yozgan javob")

    assert telegram_service._is_agent_message(_event(333, 10)) is False

    with patch.object(telegram_service, "_client") as client:
        client.send_message = AsyncMock(return_value=SimpleNamespace(id=11))
        await telegram_service.send_message(444, "tizim ogohlantirishi", as_agent=True)

    telegram_service._sending_chats.discard(444)
    assert telegram_service._is_agent_message(_event(444, 11)) is True


@pytest.mark.asyncio
async def test_id_memory_is_bounded():
    from app.services.telegram_service import AGENT_SENT_MEMORY

    with patch.object(telegram_service, "_client") as client:
        for i in range(AGENT_SENT_MEMORY + 5):
            client.send_message = AsyncMock(return_value=SimpleNamespace(id=i))
            await telegram_service._send_as_agent(555, f"javob {i}")

    assert len(telegram_service._agent_sent_ids) == AGENT_SENT_MEMORY
    assert 0 not in telegram_service._agent_sent_ids            # eng eskisi chiqib ketdi
    assert AGENT_SENT_MEMORY + 4 in telegram_service._agent_sent_ids

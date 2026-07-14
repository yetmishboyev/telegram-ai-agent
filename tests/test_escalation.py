"""Interaktiv eskalatsiya testlari.

LLM chegarasi (escalation_agent._call_llm) mock qilinadi; Redis kunlik hisobi
va ai_service quvuri haqiqiy ishlaydi.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.ai_service import ai_service
from app.services.escalation_service import escalation_service
from app.ai.agents.escalation_agent import escalation_agent
from app.ai.agents.classifier_agent import ClassificationResult, MessageCategory
from app.ai.agents.analysis_agent import MessageAnalysis
from app.ai.prompts.system_prompt import IMPORTANT_RESPONSES
from app.database.redis import get_redis


def _analysis(**overrides):
    base = dict(
        sentiment="neutral", intent="request", importance=0.8,
        threat_level="none", is_spam=False, is_phishing=False,
        is_manipulative=False, is_toxic=False, should_respond=True,
        confidence=0.95, detected_language="uz",
    )
    base.update(overrides)
    return MessageAnalysis(**base)


@pytest.fixture
def important_pipeline():
    """Xabarni IMPORTANT deb klassifikatsiya qiladigan quvurni mock qiladi."""
    classification = ClassificationResult(
        category=MessageCategory.IMPORTANT, language="uz", confidence=0.9,
        should_notify_owner=True, reason="hamkorlik taklifi",
    )
    with patch("app.services.ai_service.analysis_agent.analyze_message", AsyncMock(return_value=_analysis())), \
         patch("app.services.ai_service.analysis_agent.extract_facts", AsyncMock(return_value=[])), \
         patch("app.services.ai_service.classifier_agent.classify", AsyncMock(return_value=classification)), \
         patch("app.services.ai_service.memory_manager.build_context", AsyncMock(return_value=([], ""))), \
         patch("app.services.ai_service.memory_manager.add_exchange", AsyncMock()), \
         patch("app.services.notification_service.notification_service.notify_important", AsyncMock()):
        yield


@pytest.mark.asyncio
async def test_important_uses_interactive_escalation(db_session, important_pipeline):
    """IMPORTANT xabar statik shablon emas, LLM generatsiya qilgan javobni qaytaradi."""
    generated = "Hamkorlik taklifingizni Shaxzodbekka yetkazdim 👍 Imkon topishi bilan javob beradi."
    with patch.object(escalation_agent, "_call_llm", AsyncMock(return_value=generated)) as mock_llm:
        msg, response = await ai_service.process_message(
            db=db_session,
            telegram_id=900010001,
            text="Assalomu alaykum, hamkorlik bo'yicha taklifim bor edi.",
        )

    assert response == generated
    assert response != IMPORTANT_RESPONSES["uz"]  # statik shablon EMAS
    mock_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_escalation_falls_back_to_static_on_llm_error(db_session, important_pipeline):
    """LLM xato bersa, statik shablonga qaytadi — javobsiz qolmaydi."""
    with patch.object(escalation_agent, "_call_llm", AsyncMock(side_effect=RuntimeError("LLM down"))):
        msg, response = await ai_service.process_message(
            db=db_session,
            telegram_id=900010002,
            text="Investitsiya masalasida gaplashmoqchi edim.",
        )

    assert response is not None
    assert IMPORTANT_RESPONSES["uz"] in response


@pytest.mark.asyncio
async def test_daily_count_increments_per_user():
    """Kunlik hisob har murojaatda oshadi — takror yozgan tanilib turadi."""
    telegram_id = 900010003
    r = await get_redis()
    await r.delete(escalation_service._count_key(telegram_id))

    first = await escalation_service._incr_daily_count(telegram_id)
    second = await escalation_service._incr_daily_count(telegram_id)

    assert first == 1
    assert second == 2

    await r.delete(escalation_service._count_key(telegram_id))


@pytest.mark.asyncio
async def test_escalation_count_reaches_agent():
    """build_reply agentga to'g'ri kunlik hisobni uzatadi (2-marta = takror ohang)."""
    from app.database.models import TelegramUser

    telegram_id = 900010004
    r = await get_redis()
    await r.delete(escalation_service._count_key(telegram_id))

    user = TelegramUser(telegram_id=telegram_id, first_name="Test", relationship_type="colleague")

    with patch.object(escalation_agent, "generate", AsyncMock(return_value="ok")) as mock_gen, \
         patch("app.services.escalation_service.get_current_status", AsyncMock(return_value=None)), \
         patch("app.services.escalation_service.short_term_memory.get_recent", AsyncMock(return_value=[])):
        await escalation_service.build_reply(user, "birinchi", "uz")
        await escalation_service.build_reply(user, "yana yozdim", "uz")

    assert mock_gen.await_args_list[0].kwargs["escalation_count"] == 1
    assert mock_gen.await_args_list[1].kwargs["escalation_count"] == 2

    await r.delete(escalation_service._count_key(telegram_id))

"""FAQ / bilim bazasi testlari."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.faq_service import faq_service
from app.ai.agents.faq_agent import faq_agent
from app.services.ai_service import ai_service
from app.ai.agents.classifier_agent import ClassificationResult, MessageCategory
from app.ai.agents.analysis_agent import MessageAnalysis


def _analysis(**o):
    base = dict(
        sentiment="neutral", intent="question", importance=0.6, threat_level="none",
        is_spam=False, is_phishing=False, is_manipulative=False, is_toxic=False,
        should_respond=True, confidence=0.9, detected_language="uz",
    )
    base.update(o)
    return MessageAnalysis(**base)


@pytest.mark.asyncio
async def test_faq_agent_no_answer_returns_none():
    with patch.object(faq_agent, "_call_llm", AsyncMock(return_value="NO_ANSWER")):
        r = await faq_agent.generate("savol", "faq savol", "faq javob", "uz")
    assert r is None


@pytest.mark.asyncio
async def test_faq_agent_returns_grounded_answer():
    with patch.object(
        faq_agent, "_call_llm", AsyncMock(return_value="Narx loyihaga bog'liq.")
    ):
        r = await faq_agent.generate("qancha turadi", "narx qancha", "loyihaga bog'liq", "uz")
    assert r == "Narx loyihaga bog'liq."


@pytest.mark.asyncio
async def test_try_answer_none_when_no_match():
    with patch.object(faq_service, "search", AsyncMock(return_value=None)):
        r = await faq_service.try_answer("tasodifiy savol", "uz")
    assert r is None


@pytest.mark.asyncio
async def test_try_answer_uses_agent_on_match():
    match = {"question": "narx qancha", "answer": "loyihaga bog'liq", "faq_id": 1, "similarity": 0.8}
    with patch.object(faq_service, "search", AsyncMock(return_value=match)), \
         patch.object(faq_agent, "generate", AsyncMock(return_value="Narx loyihaga bog'liq.")):
        r = await faq_service.try_answer("qancha turadi", "uz")
    assert r == "Narx loyihaga bog'liq."


@pytest.mark.asyncio
async def test_process_message_faq_answers_before_escalation(db_session):
    """FAQ javobi bo'lsa, IMPORTANT bo'lsa ham eskalatsiya emas, FAQ qaytadi."""
    classification = ClassificationResult(
        category=MessageCategory.IMPORTANT, language="uz",
        confidence=0.9, should_notify_owner=True,
    )
    with patch("app.services.ai_service.analysis_agent.analyze_message", AsyncMock(return_value=_analysis())), \
         patch("app.services.ai_service.analysis_agent.extract_facts", AsyncMock(return_value=[])), \
         patch("app.services.ai_service.classifier_agent.classify", AsyncMock(return_value=classification)), \
         patch("app.services.ai_service.memory_manager.build_context", AsyncMock(return_value=([], ""))), \
         patch("app.services.ai_service.memory_manager.add_exchange", AsyncMock()), \
         patch("app.services.ai_service.faq_service.try_answer", AsyncMock(return_value="FAQ javob")), \
         patch("app.services.ai_service.escalation_service.build_reply",
               AsyncMock(side_effect=AssertionError("eskalatsiya chaqirilmasligi kerak edi"))):
        msg, response = await ai_service.process_message(
            db=db_session, telegram_id=900030001, text="Konsalting narxi qancha?"
        )
    assert response == "FAQ javob"


@pytest.mark.asyncio
async def test_add_search_remove_faq_integration():
    """add_faq → chroma index → search → remove (haqiqiy embedder + chroma)."""
    faq_id = await faq_service.add_faq(
        "Konsalting xizmati narxi qancha turadi?",
        "Konsalting narxi loyiha hajmiga bog'liq, aniq narx uchun bog'laning.",
    )
    try:
        match = await faq_service.search("konsalting qancha pul turadi")
        assert match is not None, "yaqin savol topilishi kerak edi"
        assert match["faq_id"] == faq_id
        assert "loyiha" in match["answer"].lower()
    finally:
        await faq_service.remove_faq(faq_id)

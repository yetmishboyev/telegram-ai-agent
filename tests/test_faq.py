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


def _cand(q, a, faq_id=1, sim=0.8):
    return {"question": q, "answer": a, "faq_id": faq_id, "similarity": sim}


@pytest.mark.asyncio
async def test_faq_agent_no_answer_returns_none():
    with patch.object(faq_agent, "_call_llm", AsyncMock(return_value="NO_ANSWER")):
        r = await faq_agent.generate("savol", [_cand("faq savol", "faq javob")], "uz")
    assert r is None


@pytest.mark.asyncio
async def test_faq_agent_returns_grounded_answer():
    with patch.object(
        faq_agent, "_call_llm", AsyncMock(return_value="Narx loyihaga bog'liq.")
    ):
        r = await faq_agent.generate(
            "qancha turadi", [_cand("narx qancha", "loyihaga bog'liq")], "uz")
    assert r == "Narx loyihaga bog'liq."


@pytest.mark.asyncio
async def test_faq_agent_receives_every_candidate():
    """Barcha nomzodlar promptga tushishi kerak — to'g'risi 2-o'rinda bo'lishi mumkin."""
    captured = {}

    async def spy(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "javob"

    cands = [_cand("noto'g'ri savol", "noto'g'ri javob", 1, 0.67),
             _cand("to'g'ri savol", "to'g'ri javob", 2, 0.61)]
    with patch.object(faq_agent, "_call_llm", AsyncMock(side_effect=spy)):
        await faq_agent.generate("foydalanuvchi savoli", cands, "uz")

    assert "to'g'ri javob" in captured["prompt"], "2-nomzod ham promptda bo'lishi shart"
    assert "noto'g'ri javob" in captured["prompt"]
    assert "[BILIM 1]" in captured["prompt"] and "[BILIM 2]" in captured["prompt"]


@pytest.mark.asyncio
async def test_faq_agent_none_without_candidates():
    assert await faq_agent.generate("savol", [], "uz") is None


@pytest.mark.asyncio
async def test_try_answer_none_when_no_match():
    with patch.object(faq_service, "search", AsyncMock(return_value=[])):
        r = await faq_service.try_answer("tasodifiy savol", "uz")
    assert r is None


@pytest.mark.asyncio
async def test_try_answer_uses_agent_on_match():
    with patch.object(faq_service, "search",
                      AsyncMock(return_value=[_cand("narx qancha", "loyihaga bog'liq")])), \
         patch.object(faq_agent, "generate", AsyncMock(return_value="Narx loyihaga bog'liq.")):
        r = await faq_service.try_answer("qancha turadi", "uz")
    assert r == "Narx loyihaga bog'liq."


@pytest.mark.asyncio
async def test_try_answer_skips_candidates_without_answer():
    with patch.object(faq_service, "search", AsyncMock(return_value=[
            {"question": "q", "answer": "", "faq_id": 1, "similarity": 0.9}])):
        assert await faq_service.try_answer("savol", "uz") is None


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
        matches = await faq_service.search("konsalting qancha pul turadi")
        assert matches, "yaqin savol topilishi kerak edi"
        ids = [m["faq_id"] for m in matches]
        assert faq_id in ids, f"qo'shilgan FAQ nomzodlar orasida bo'lishi kerak: {ids}"
    finally:
        await faq_service.remove_faq(faq_id)

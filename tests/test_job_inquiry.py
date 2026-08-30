"""Ish o'rni so'rovi — CV faqat shu holatda so'raladi.

Ega hech kimdan CV yoki obyektivka so'ramaydi. Yagona istisno: kimdir bo'sh
ish o'rni / ishga kirish haqida yozganda. Bu testlar ikkala tomonni ham
qamrab oladi — aniqlanishi kerak bo'lganlar va aniqlanmasligi kerak bo'lganlar.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.job_inquiry import is_job_inquiry, is_job_offer, get_job_inquiry_reply
from app.ai.agents.analysis_agent import MessageAnalysis
from app.ai.agents.classifier_agent import ClassificationResult, MessageCategory
from app.services.ai_service import ai_service


def _analysis(**o):
    base = dict(
        sentiment="neutral", intent="question", importance=0.6, threat_level="none",
        is_spam=False, is_phishing=False, is_manipulative=False, is_toxic=False,
        should_respond=True, confidence=0.9, detected_language="uz",
    )
    base.update(o)
    return MessageAnalysis(**base)


# ─── aniqlagich ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # o'zbekcha
    "Assalomu alaykum, sizlarda bo'sh ish o'rni bormi?",
    "Sizda bosh ish orni bormi",                        # apostrofsiz yozilgan
    "Ishga kirsam bo'ladimi?",
    "Meni ishga olasizmi?",
    "Vakansiya bormi?",
    "Ish o'rni bo'yicha murojaat qilmoqchiman",
    "Sizda ish bormi?",
    "Ish qidiryapman, yordam bera olasizmi",
    "Sizda ishlamoqchiman",
    "Amaliyot o'tsam bo'ladimi?",
    "CV mni yuborsam bo'ladimi?",
    # ruscha
    "Здравствуйте, есть ли вакансии?",
    "Ищу работу, можно к вам?",
    "Хочу устроиться на работу",
    "Есть ли работа для меня?",
    # inglizcha
    "Do you have any vacancies?",
    "Are you hiring right now?",
    "I'd like to apply for a job",
    "Any openings in your team?",
    "Is an internship possible?",
])
def test_detects_job_inquiry(text):
    assert is_job_inquiry(text) is True


@pytest.mark.parametrize("text", [
    "Assalomu alaykum, qalaysiz?",
    "Hamkorlik qilmoqchiman, loyihangiz haqida gaplashsak",
    "Ertaga uchrashuvga ulgurasizmi?",
    "AI bo'yicha maslahat kerak edi",
    "Ishlaringiz qanday ketyapti?",              # "ish" bor, lekin so'rov emas
    "Bu ishga kirishdim, ertaga tugataman",      # "ishga kirish-" boshqa ma'noda
    "Konferensiyaga taklif qilmoqchiman",
    "Kitobingizni o'qidim, juda yoqdi",
    "Спасибо за информацию!",
    "Can we schedule a meeting next week?",
])
def test_ignores_non_job_messages(text):
    assert is_job_inquiry(text) is False


@pytest.mark.parametrize("text", [
    # Kimdir EGAGA ish taklif qilyapti — bu so'rov emas, muhim murojaat
    "Assalomu alaykum, siz uchun ish bor",
    "Sizni ishga qabul qilmoqchimiz",
    "Bizda siz uchun ish o'rni bor",
    "Sizga yaxshi lavozim taklif qilmoqchimiz",
    "Предлагаем вам работу в нашей компании",
    "We would like to offer you a position",
])
def test_job_offer_is_not_an_inquiry(text):
    """Taklif eskalatsiyaga tushishi kerak, CV so'ralishi emas."""
    assert is_job_offer(text) is True
    assert is_job_inquiry(text) is False


def test_reply_asks_for_the_cv_as_a_file():
    """Matn sifatida yuborilgan obyektivka maxfiy filtrga tushib yo'qolardi."""
    uz = get_job_inquiry_reply("uz")
    assert "FAYL" in uz
    assert "ФАЙЛОМ" in get_job_inquiry_reply("ru")
    assert "FILE" in get_job_inquiry_reply("en")


def test_reply_asks_for_cv_in_each_language():
    assert "CV" in get_job_inquiry_reply("uz")
    assert "резюме" in get_job_inquiry_reply("ru")
    assert "CV" in get_job_inquiry_reply("en")
    # Noma'lum til → o'zbekcha
    assert get_job_inquiry_reply("de") == get_job_inquiry_reply("uz")


def test_reply_makes_no_promise():
    """Agent vakansiya bor deb va'da bermaydi — u buni bilmaydi."""
    reply = get_job_inquiry_reply("uz")
    for promise in ("qabul qilamiz", "ish beramiz", "vakansiya bor"):
        assert promise not in reply.lower()


# ─── quvurdagi o'rni ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_inquiry_asks_for_cv_and_notifies_owner(db_session):
    classification = ClassificationResult(
        category=MessageCategory.IMPORTANT, language="uz",
        confidence=0.9, should_notify_owner=True,
    )
    with patch("app.services.ai_service.analysis_agent.analyze_message", AsyncMock(return_value=_analysis())), \
         patch("app.services.ai_service.classifier_agent.classify", AsyncMock(return_value=classification)), \
         patch("app.services.ai_service.memory_manager.build_context", AsyncMock(return_value=([], ""))), \
         patch("app.services.ai_service.memory_manager.add_exchange", AsyncMock()), \
         patch("app.services.ai_service.faq_service.try_answer", AsyncMock(return_value=None)), \
         patch("app.services.ai_service.escalation_service.build_reply",
               AsyncMock(side_effect=AssertionError("ish so'rovida eskalatsiya emas, CV so'ralishi kerak"))), \
         patch.object(ai_service, "_notify_owner", AsyncMock()) as notify:
        msg, response = await ai_service.process_message(
            db=db_session, telegram_id=900060001,
            text="Assalomu alaykum, sizlarda bo'sh ish o'rni bormi?",
        )
        await asyncio.sleep(0)  # create_task ishga tushishi uchun

    assert "CV" in response
    assert msg.agent_response == response
    notify.assert_called_once()
    assert "Ish o'rni" in notify.call_args.args[2]


@pytest.mark.asyncio
async def test_non_job_important_message_goes_to_escalation(db_session):
    """Oddiy muhim xabar CV so'ramaydi — eski xulq saqlanadi."""
    classification = ClassificationResult(
        category=MessageCategory.IMPORTANT, language="uz",
        confidence=0.9, should_notify_owner=True,
    )
    with patch("app.services.ai_service.analysis_agent.analyze_message", AsyncMock(return_value=_analysis())), \
         patch("app.services.ai_service.classifier_agent.classify", AsyncMock(return_value=classification)), \
         patch("app.services.ai_service.memory_manager.build_context", AsyncMock(return_value=([], ""))), \
         patch("app.services.ai_service.memory_manager.add_exchange", AsyncMock()), \
         patch("app.services.ai_service.faq_service.try_answer", AsyncMock(return_value=None)), \
         patch("app.services.ai_service.escalation_service.build_reply",
               AsyncMock(return_value="Xabaringizni yetkazdim.")), \
         patch.object(ai_service, "_notify_owner", AsyncMock()):
        _, response = await ai_service.process_message(
            db=db_session, telegram_id=900060002,
            text="Hamkorlik bo'yicha taklifim bor edi",
        )
        await asyncio.sleep(0)

    assert response == "Xabaringizni yetkazdim."
    assert "CV" not in response


def test_persona_forbids_asking_for_documents():
    """Prompt qatlamidagi taqiq — generatsiya tarmog'i uchun."""
    from app.ai.prompts.system_prompt import AGENT_PERSONA, ESCALATION_PROMPT
    assert "SO'RAMA" in AGENT_PERSONA
    assert "obyektivka" in AGENT_PERSONA
    assert "SO'RAMA" in ESCALATION_PROMPT

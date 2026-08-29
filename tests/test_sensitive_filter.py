"""Maxfiy-ma'lumot filtri (`_SENSITIVE_PATTERNS`/`_detect_sensitive`) testlari.

Roadmap Faza 2, band 4-5 (Luhn tekshiruvi, JSHSHIR uchun kontekst talabi)
qo'llanilgandan keyin — avvalgi yolg'on-ijobiylar endi to'g'ri rad etiladi
(`TestFixedFalsePositives` klassiga qarang).
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.database.models import MessageType
from app.services.ai_service import ai_service, _detect_sensitive, _luhn_valid


class TestDetectsKnownCategories:
    @pytest.mark.parametrize(
        "text,expected_category",
        [
            ("Mening kartam: 4111 1111 1111 1111", "bank_card"),
            ("cvv: 123", "cvv"),
            ("pin: 1234", "pin"),
            ("password: mySecret123", "password"),
            ("parol: qwerty123", "password"),
            ("passport: AB1234567", "passport"),
            ("JSHSHIR: 12345678901234", "jshshir"),
            ("PINFL: 12345678901234", "jshshir"),
            ("login: shaxzodbek", "credentials"),
            ("otp: 483920", "otp"),
            ("0x71C7656EC7ab88b098defB751B7401B5f6d8976F", "crypto_wallet"),
        ],
    )
    def test_detects_category(self, text, expected_category):
        assert _detect_sensitive(text) == expected_category


class TestAllowsBenignMessages:
    @pytest.mark.parametrize(
        "text",
        [
            "Salom, bugun soat nechida uchrashamiz?",
            "Mening ismim Shaxzodbek",
            "2024 yilda tug'ilganman",
            "Uchrashuv 15:30 da boshlanadi",
            "Rahmat katta yordamingiz uchun",
            "Loyihaning versiyasi 1.2.3",
        ],
    )
    def test_does_not_flag_benign_text(self, text):
        assert _detect_sensitive(text) is None


class TestFixedFalsePositives:
    """Audit M-1 da aniqlangan yolg'on-ijobiylar — endi to'g'ri rad etiladi."""

    def test_arbitrary_14_digit_number_without_context_not_flagged(self):
        text = "Mening telefon raqamim 12345678901234 ga qo'ng'iroq qiling"
        assert _detect_sensitive(text) is None

    def test_non_luhn_digit_sequence_not_flagged_as_bank_card(self):
        text = "Buyurtma raqami: 123456789012345678"
        assert _detect_sensitive(text) is None


class TestLuhnValid:
    def test_known_valid_test_card_number(self):
        assert _luhn_valid("4111111111111111") is True

    def test_arbitrary_sequence_is_invalid(self):
        assert _luhn_valid("123456789012345678") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id,text", [
    # Ega hech kimdan hujjat so'ramaydi — pasport/JSHSHIR ham kutilmagan
    # ma'lumot, ular ham ogohlantirish oladi (2026-08-29).
    (900050001, "Obyektivkam: Aliyev Vali, pasport AA1234567, 1995-yil"),
    (900050002, "JSHSHIR: 12345678901234"),
    (900050004, "parol: qwerty123"),
    (900050005, "Mening kartam: 4111 1111 1111 1111"),
])
async def test_sensitive_text_gets_the_warning_and_no_notification(
    db_session, telegram_id, text
):
    with patch.object(ai_service, "_notify_owner", AsyncMock()) as notify:
        msg, reply = await ai_service.process_message(
            db=db_session, telegram_id=telegram_id, text=text,
        )
        await asyncio.sleep(0)  # create_task ishga tushishi uchun

    assert "ulashmang" in reply
    assert "qabul qilindi" not in reply
    assert msg.content == "[MAXFIY MA'LUMOT — saqlanmadi]"  # matn saqlanmaydi
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_document_is_acknowledged_without_llm(db_session):
    """CV fayl: klassifikatsiyaga bormaydi, tasdiq javobi va bildirishnoma beradi."""
    with patch.object(ai_service, "_notify_owner", AsyncMock()) as notify, \
         patch("app.services.ai_service.memory_manager.add_exchange", AsyncMock()), \
         patch("app.services.ai_service.analysis_agent.analyze_message",
               AsyncMock(side_effect=AssertionError("hujjat uchun LLM chaqirilmasligi kerak"))):
        msg, reply = await ai_service.process_message(
            db=db_session, telegram_id=900050003,
            text="📎 Hujjat yuborildi: CV_Aliyev.pdf",
            message_type=MessageType.DOCUMENT,
        )
        await asyncio.sleep(0)

    assert "qabul qilindi" in reply
    assert msg.agent_response == reply
    notify.assert_called_once()
    assert "CV_Aliyev.pdf" in notify.call_args.args[1]   # fayl nomi egaga ko'rinadi


def test_document_label_uses_the_file_name():
    from telethon.tl.types import DocumentAttributeFilename
    from app.services.telegram_service import telegram_service

    message = SimpleNamespace(
        document=SimpleNamespace(attributes=[DocumentAttributeFilename("CV_Aliyev.pdf")])
    )
    assert telegram_service._document_label(message) == "📎 Hujjat yuborildi: CV_Aliyev.pdf"

    # Nomi topilmasa umumiy yorliq
    assert telegram_service._document_label(
        SimpleNamespace(document=SimpleNamespace(attributes=[]))
    ) == "📎 Hujjat yuborildi"

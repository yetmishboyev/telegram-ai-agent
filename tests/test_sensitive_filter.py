"""Maxfiy-ma'lumot filtri (`_SENSITIVE_PATTERNS`/`_detect_sensitive`) testlari.

Roadmap Faza 2, band 4-5 (Luhn tekshiruvi, JSHSHIR uchun kontekst talabi)
qo'llanilgandan keyin — avvalgi yolg'on-ijobiylar endi to'g'ri rad etiladi
(`TestFixedFalsePositives` klassiga qarang).
"""
import pytest

from app.services.ai_service import _detect_sensitive, _luhn_valid


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

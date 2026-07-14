"""
Guardrails — ikki qatlamli xavfsizlik filtri:
  1. Input  — xabar Claude API ga borgunga qadar tekshiriladi
  2. Output — AI javobi foydalanuvchiga yuborilgunga qadar tekshiriladi
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from loguru import logger


@dataclass
class GuardrailResult:
    blocked: bool
    category: str = ""
    reply: dict[str, str] | None = None  # {uz, ru, en}


# ---------------------------------------------------------------------------
# INPUT GUARDRAILS
# ---------------------------------------------------------------------------

_INPUT_RULES: list[tuple[str, re.Pattern]] = [
    # Prompt injection / system prompt override urinishlari
    ("prompt_injection", re.compile(
        r"\b(?:ignore\s+(?:previous|all|your)\s+instructions?"
        r"|forget\s+(?:your\s+)?instructions?"
        r"|disregard\s+(?:all\s+)?(?:previous\s+)?instructions?"
        r"|override\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)"
        r"|new\s+(?:system\s+)?prompt\s*[:\-]"
        r"|pretend\s+(?:you\s+are|to\s+be)"
        r"|you\s+are\s+now\s+(?:a\s+)?(?:new\s+)?(?:AI|bot|assistant|DAN)"
        r"|act\s+as\s+(?:if\s+you\s+(?:have\s+no|are\s+without)\s+restrictions?"
        r"|an?\s+(?:unfiltered|unrestricted|uncensored))"
        r")\b",
        re.I,
    )),
    # Jailbreak urinishlari
    ("jailbreak", re.compile(
        r"\b(?:jailbreak|DAN\s+mode|developer\s+mode|god\s+mode"
        r"|no\s+(?:limits?|restrictions?|rules?|filters?)"
        r"|bypass\s+(?:safety|filter|restriction|rule)"
        r"|unlimited\s+(?:AI|mode|access)"
        r"|without\s+(?:any\s+)?(?:safety|ethical)\s+(?:filters?|guidelines?|restrictions?)"
        r"|unrestricted\s+(?:AI|mode|version)"
        r")\b",
        re.I,
    )),
    # System prompt ni o'qishga urinish
    ("system_extraction", re.compile(
        r"(?:show|print|repeat|reveal|display|tell\s+me|what\s+(?:is|are))\s+"
        r"(?:your\s+)?(?:system\s+prompt|instructions?|prompt|initial\s+prompt"
        r"|base\s+prompt|original\s+(?:prompt|instructions?))",
        re.I,
    )),
    # O'z-o'ziga zarar yetkazish
    ("self_harm", re.compile(
        r"\b(?:how\s+to\s+(?:kill\s+(?:myself|yourself)|commit\s+suicide|end\s+(?:my|your)\s+life"
        r"|hurt\s+(?:myself|yourself))"
        r"|suicide\s+(?:method|guide|how)"
        r"|ways?\s+to\s+(?:die|kill\s+(?:myself|yourself))"
        r")\b",
        re.I,
    )),
    # Qurol tayyorlash
    ("weapon_synthesis", re.compile(
        r"\b(?:how\s+to\s+(?:make|build|create|synthesize)\s+"
        r"(?:a\s+)?(?:bomb|explosive|gun|weapon|poison|nerve\s+agent|chemical\s+weapon)"
        r"|(?:bomb|explosive|gun)\s+(?:making|building|instructions?|guide|recipe)"
        r")\b",
        re.I,
    )),
    # Noqonuniy faoliyat
    ("illegal_activity", re.compile(
        r"\b(?:how\s+to\s+(?:hack\s+(?:into|a)|crack\s+(?:a\s+)?(?:password|account)"
        r"|steal\s+(?:credit\s+card|identity|money|data)"
        r"|launder\s+money|make\s+(?:fake|counterfeit)\s+(?:money|currency|documents?)"
        r"|bypass\s+(?:security|authentication|2fa|mfa))"
        r")\b",
        re.I,
    )),
]

_INPUT_REPLIES: dict[str, dict[str, str]] = {
    "prompt_injection": {
        "uz": "Bu xabar AI yo'riqnomalarini o'zgartirishga urinish sifatida aniqlandi. Xabar bloklandi.",
        "ru": "Это сообщение определено как попытка изменить инструкции AI. Сообщение заблокировано.",
        "en": "This message was identified as an attempt to override AI instructions. Message blocked.",
    },
    "jailbreak": {
        "uz": "Bu so'rov AI cheklovlarini chetlab o'tishga urinish. Xabar bloklandi.",
        "ru": "Этот запрос является попыткой обойти ограничения AI. Сообщение заблокировано.",
        "en": "This request attempts to bypass AI safety restrictions. Message blocked.",
    },
    "system_extraction": {
        "uz": "Tizim ko'rsatmalarini ochib berishga ruxsat yo'q.",
        "ru": "Раскрытие системных инструкций не разрешено.",
        "en": "Revealing system instructions is not permitted.",
    },
    "self_harm": {
        "uz": (
            "Bu mavzuda yordam bera olmayman. "
            "Agar o'zingizni yomon his qilsangiz, yaqinlaringizga murojaat qiling "
            "yoki 1800 raqamiga qo'ng'iroq qiling (psixologik yordam xizmati)."
        ),
        "ru": (
            "Я не могу помочь с этим запросом. "
            "Если вам плохо, обратитесь к близким или позвоните на линию психологической помощи."
        ),
        "en": (
            "I cannot help with this request. "
            "If you're struggling, please reach out to someone you trust or contact a crisis helpline."
        ),
    },
    "weapon_synthesis": {
        "uz": "Bu so'rovga javob berish mumkin emas.",
        "ru": "На этот запрос нельзя ответить.",
        "en": "This request cannot be answered.",
    },
    "illegal_activity": {
        "uz": "Noqonuniy faoliyatga yordam bera olmayman. Xabar bloklandi.",
        "ru": "Я не могу помочь с незаконной деятельностью. Сообщение заблокировано.",
        "en": "I cannot assist with illegal activities. Message blocked.",
    },
}

_DEFAULT_INPUT_REPLY = {
    "uz": "Bu so'rovga javob berib bo'lmaydi. Xabar bloklandi.",
    "ru": "На этот запрос невозможно ответить. Сообщение заблокировано.",
    "en": "This request cannot be processed. Message blocked.",
}


def get_manipulation_reply(lang: str) -> str:
    """LLM (analysis_agent) istalgan tilda prompt injection/jailbreak aniqlaganda ishlatiladigan javob."""
    replies = _INPUT_REPLIES["prompt_injection"]
    return replies.get(lang, replies["uz"])


def check_input(text: str) -> GuardrailResult:
    """Kiruvchi xabarni tekshiradi. Bloklansa GuardrailResult(blocked=True) qaytaradi."""
    for category, pattern in _INPUT_RULES:
        if pattern.search(text):
            logger.warning(f"[Guardrail/input] Bloklandi: category={category}")
            return GuardrailResult(
                blocked=True,
                category=category,
                reply=_INPUT_REPLIES.get(category, _DEFAULT_INPUT_REPLY),
            )
    return GuardrailResult(blocked=False)


# ---------------------------------------------------------------------------
# OUTPUT GUARDRAILS
# ---------------------------------------------------------------------------

_OUTPUT_RULES: list[tuple[str, re.Pattern]] = [
    # System prompt sizib chiqishi
    ("system_leak", re.compile(
        r"(?:my\s+(?:system\s+)?(?:prompt|instructions?)\s+(?:is|are|say|state)"
        # "I was instructed to help/answer/assist..." kabi zararsiz gaplarni
        # bloklamaslik uchun faqat sirni oshkor qilish bilan bog'liq fe'llar
        # (reveal/share/disclose va h.k.) bilan birga kelgandagina mos keladi.
        r"|(?:I\s+(?:am|was)\s+(?:told|instructed|programmed)\s+(?:not\s+)?to"
        r"\s+(?:\w+\s+){0,2}(?:reveal|share|disclose|discuss|repeat|hide|conceal)\b)"
        r"|(?:system\s+prompt\s*[:\-]))",
        re.I,
    )),
    # Aniq zararli kontent
    ("harmful_output", re.compile(
        r"(?:here(?:'s|\s+is)\s+(?:how\s+to\s+)?(?:make|build|create)\s+"
        r"(?:a\s+)?(?:bomb|explosive|weapon|poison)"
        r"|step[\s\-]by[\s\-]step\s+(?:guide\s+)?(?:to\s+)?(?:kill|harm|attack))",
        re.I,
    )),
]

OUTPUT_FALLBACK: dict[str, str] = {
    "uz": "Kechirasiz, bu savolga javob bera olmayman.",
    "ru": "Извините, я не могу ответить на этот вопрос.",
    "en": "Sorry, I cannot respond to this request.",
}


def check_output(text: str) -> GuardrailResult:
    """AI javobini tekshiradi. Muammo topilsa bloklaydi."""
    for category, pattern in _OUTPUT_RULES:
        if pattern.search(text):
            logger.warning(f"[Guardrail/output] Bloklandi: category={category}")
            return GuardrailResult(blocked=True, category=category)
    return GuardrailResult(blocked=False)


def get_output_fallback(lang: str) -> str:
    return OUTPUT_FALLBACK.get(lang, OUTPUT_FALLBACK["uz"])


_EN_MARKER_RE = re.compile(
    r"\b(?:the|is|are|you|your|what|how|why|when|where"
    r"|hello|hi|hey|thanks|thank|please|yes|okay|ok)\b",
    re.I,
)


def get_lang(text: str) -> str:
    """Matndan tilni taxminiy aniqlaydi.

    Lotin yozuvidagi matn odatiy inglizcha so'zlarni o'z ichiga olsagina "en"
    deb belgilanadi; aks holda (loyihaning asosiy auditoriyasiga mos) "uz"
    standart qiymat sifatida qaytariladi. Ilgari bu yerda `[o'ʻ]` belgilar
    to'plami ishlatilgan edi — u xato ravishda oddiy "o" harfini ham tutib,
    "o" harfi bo'lgan istalgan inglizcha matnni "uz" deb belgilagan.
    """
    if re.search(r"[а-яёА-ЯЁ]", text):
        return "ru"
    if re.search(r"[a-zA-Z]", text) and _EN_MARKER_RE.search(text):
        return "en"
    return "uz"

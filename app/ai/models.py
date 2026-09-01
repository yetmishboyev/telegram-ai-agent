"""Model qatlamlari, imkoniyatlari va narxi.

Bitta model hamma ishni bajarishi shart emas: spam tekshiruvi Opus talab
qilmaydi, kanal posti esa Haiku'da sayozlashadi. Har agent o'ziga mos
qatlamni tanlaydi, qatlam esa sozlamalardagi aniq model ID'siga aylanadi.
"""
from enum import Enum


class ModelTier(str, Enum):
    """Agent qaysi og'irlikdagi modelga muhtoj."""

    FAST = "fast"          # tasniflash, tahlil, fakt ajratish — ko'p va qisqa
    BALANCED = "balanced"  # foydalanuvchiga javob, FAQ, eskalatsiya
    DEEP = "deep"          # kanal kontenti, curation, strategiya, sintez


# ─── imkoniyatlar ─────────────────────────────────────────────────────────────
# `output_config.effort` ni tushunadigan modellar. 4.6-oila ham kiradi
# (`low`/`medium`/`high`/`max`), 4.7+ va 5-oila `xhigh` ni ham qo'shadi.
# Sonnet 4.5 va Haiku 4.5 esa `effort` ni RAD ETADI.
_EFFORT_MODEL_PREFIXES = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
)


def _sdk_accepts_temperature() -> bool:
    """O'rnatilgan SDK `temperature` ni umuman qabul qiladimi.

    Anthropic SDK 1.x da `temperature`, `top_p` va `top_k` BUTUNLAY olib
    tashlangan — yuborilsa `TypeError` ko'tariladi (API xatosi emas, ya'ni
    qayta urinish yordam bermaydi). 0.x da esa ular hamon bor.

    Shuning uchun modelga qarab qaror qilish YETARLI EMAS: SDK imkoniyati
    ham tekshirilishi kerak. Bu 2026-08-30 da produksiyada aniqlandi —
    `anthropic>=0.104.0` pini 1.2.0 ni tortib keldi va eski model uchun
    yuborilgan `temperature` har javobni yiqitdi.
    """
    try:
        import inspect
        from anthropic.resources.messages import AsyncMessages
        return "temperature" in inspect.signature(AsyncMessages.create).parameters
    except Exception:
        return False  # aniqlab bo'lmasa — yubormaymiz (xavfsiz tomon)


# Bir marta hisoblanadi: SDK jarayon davomida o'zgarmaydi.
SDK_ACCEPTS_TEMPERATURE = _sdk_accepts_temperature()


def uses_effort(model: str) -> bool:
    """Model `output_config.effort` ni qo'llab-quvvatlaydimi."""
    return model.startswith(_EFFORT_MODEL_PREFIXES)


def sampling_mode(model: str) -> str:
    """Chaqiruvda nima yuborilishini aytadi: 'effort', 'temperature' yoki 'none'.

    'none' — model `effort` ni bilmaydi (masalan Haiku 4.5) VA SDK
    `temperature` ni qabul qilmaydi. Bunda hech nima yuborilmaydi va model
    o'z standart qiymatida ishlaydi. Bu sifatni biroz boshqarib bo'lmasligini
    bildiradi, lekin chaqiruv ISHLAYDI — yiqilishdan ko'ra yaxshiroq.
    """
    if uses_effort(model):
        return "effort"
    if SDK_ACCEPTS_TEMPERATURE:
        return "temperature"
    return "none"


# `thinking` yoqilgan modellarda fikrlash tokenlari HAM `max_tokens` byudjetidan
# yeyiladi. Byudjet kichik bo'lsa model o'ylab tugatadi va MATN BLOKI umuman
# qaytmaydi — chaqiruv xato bermaydi, shunchaki bo'sh satr keladi.
#
# O'lchangan (2026-08-30, claude-opus-5): max_tokens=100 → bo'sh; 300 → ishlaydi.
# Chegara ehtiyot zaxirasi bilan olingan, chunki adaptive thinking har so'rovda
# qancha o'ylashni O'ZI hal qiladi — bugun yetgan byudjet ertaga yetmasligi mumkin.
#
# `max_tokens` bu SHIFT, sarf emas: qisqa javobda ortiqcha token yozilmaydi.
# 1024 kam edi. Post generatorlari shu shiftda ishlab, ba'zan MUTLAQO BO'SH
# javob qaytarardi: model butun byudjetni fikrlashga sarflab, matn yozishga
# token qolmasdi. Bo'sh qoralama muharrir qatlamiga borib, u yerdan "matn
# yuborilmabdi" degan javob post o'rniga chiqardi. Shift sarf emas — qisqa
# javobda ortiqcha token yozilmaydi, shuning uchun uni kengaytirish tekin.
# O'lchandi: bitta post uchun fikrlash 2200-3000 token oladi, matnning
# o'zi esa ~250. Shift shundan sezilarli baland turishi kerak.
MIN_OUTPUT_TOKENS_WITH_THINKING = 8192

_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")

# Qatlam uchun ruxsat etilgan ENG YUQORI effort. Sabab — kechikish: foydalanuvchi
# javobini kutib turadi, shuning uchun BALANCED qatlam `high` ga chiqmaydi.
#
# DEEP ilgari `high` edi. O'lchov shuni ko'rsatdiki, bitta 110 so'zlik post
# uchun fikrlash 2200-3000 token yeydi, matnning o'zi esa ~250 — ya'ni
# chuqurlikning katta qismi kanal postiga qaytmaydi. Post yozish uzoq
# muhokama talab qiladigan vazifa emas; `medium` shu ish uchun yetarli va
# xarajatni sezilarli tushiradi. Sifat pasaysa — bu yerdan qaytariladi.
_TIER_EFFORT_CAP = {
    "fast":     "low",
    "balanced": "medium",
    "deep":     "medium",
}


def effort_for_temperature(temperature: float, tier: str | None = None) -> str:
    """Eski `temperature` niyatini `effort` darajasiga o'giradi.

    Loyihada 20 dan ortiq chaqiruv o'z temperature'ini uzatadi va u yozilgan
    niyatni bildiradi: 0.1 — qat'iy tasniflash, 0.9 — xilma-xil ijodiy matn.
    Har chaqiruv joyini tahrirlash o'rniga niyatni shu yerda tarjima qilamiz.

    `tier` berilsa natija o'sha qatlamning shifti bilan cheklanadi.
    """
    if temperature <= 0.3:
        level = "low"
    elif temperature <= 0.6:
        level = "medium"
    else:
        level = "high"

    cap = _TIER_EFFORT_CAP.get(tier or "")
    if cap and _EFFORT_ORDER.index(level) > _EFFORT_ORDER.index(cap):
        return cap
    return level


# ─── narx ─────────────────────────────────────────────────────────────────────
# ($ / 1M kiruvchi token, $ / 1M chiquvchi token) — Anthropic birlamchi API
# tariflari. Ro'yxatda yo'q model uchun xarajat hisoblanmaydi (None qaytadi),
# taxmin qilinmaydi.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5":     (5.0, 25.0),
    "claude-opus-4-8":   (5.0, 25.0),
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-opus-4-6":   (5.0, 25.0),
    "claude-sonnet-5":   (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-fable-5":    (10.0, 50.0),
}

# Kesh koeffitsientlari: yozish ~1.25×, o'qish ~0.1× kiruvchi tarifdan
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


def estimate_cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """Bitta chaqiruvning taxminiy narxi. Model tarifi noma'lum bo'lsa None."""
    rates = None
    for name, value in _PRICING.items():
        if model.startswith(name):
            rates = value
            break
    if rates is None:
        return None

    in_rate, out_rate = rates
    total = (
        input_tokens * in_rate
        + cache_write_tokens * in_rate * _CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * in_rate * _CACHE_READ_MULTIPLIER
        + output_tokens * out_rate
    ) / 1_000_000
    return round(total, 8)


def min_output_tokens(model: str) -> int:
    """Model uchun `max_tokens` ning eng past xavfsiz qiymati."""
    return MIN_OUTPUT_TOKENS_WITH_THINKING if uses_effort(model) else 1

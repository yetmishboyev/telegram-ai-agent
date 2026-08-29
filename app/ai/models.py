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
# Yangi avlod modellari (5-oila va Opus 4.7+) sampling parametrlarini QABUL
# QILMAYDI — `temperature` yuborilsa API 400 qaytaradi. Ular chiqish sifatini
# `output_config.effort` orqali boshqaradi. Eski modellar esa `effort` ni
# tushunmaydi. Shuning uchun ikkovi bir-birini istisno qiladi.
_EFFORT_MODEL_PREFIXES = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
)


def uses_effort(model: str) -> bool:
    """Model `output_config.effort` ishlatadimi (va `temperature` ni rad etadimi)."""
    return model.startswith(_EFFORT_MODEL_PREFIXES)


_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")

# Qatlam uchun ruxsat etilgan ENG YUQORI effort. Sabab — kechikish: foydalanuvchi
# javobini kutib turadi, shuning uchun BALANCED qatlam `high` ga chiqmaydi.
# Kanal posti esa fon vazifasi, u yerda sifat kechikishdan muhimroq.
_TIER_EFFORT_CAP = {
    "fast":     "low",
    "balanced": "medium",
    "deep":     "high",
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

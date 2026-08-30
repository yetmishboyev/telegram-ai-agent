"""LLM chaqiruvlari hisobi — token, narx, kechikish.

`AgentLog` jadvali loyihaning boshidan beri mavjud edi, lekin unga hech kim
yozmasdi. Endi har LLM chaqiruvi shu yerga tushadi: qaysi agent, qaysi model,
qancha token, qancha pul, necha millisekund. Migratsiya kerak emas —
o'lchovlar `extra` JSON ustunida yashaydi.

Yozish har doim fon vazifasida va har doim try/except ichida: hisob yuritish
javob berishni hech qachon sekinlashtirmasligi yoki buzmasligi kerak.
"""
import asyncio
from contextlib import contextmanager

from loguru import logger

from app.ai.models import estimate_cost_usd

# Haqiqiy trafik va qayta yurgizish (replay) qatorlari AJRATILADI. Aks holda
# "kunlik xarajat" ma'nosini yo'qotardi: qatorlarning yarmi foydalanuvchidan
# emas, o'lchov skriptidan kelgan bo'lardi. Dashboard standart holatda faqat
# `llm.` prefiksini sanaydi.
COMPONENT_PREFIX = "llm"
SYNTHETIC_PREFIX = "replay"

_synthetic_mode = False


@contextmanager
def synthetic_run():
    """Shu blok ichidagi chaqiruvlar sun'iy deb belgilanadi.

    Faqat bitta jarayonli o'lchov skripti uchun — server bu bayroqni hech
    qachon yoqmaydi.
    """
    global _synthetic_mode
    previous = _synthetic_mode
    _synthetic_mode = True
    try:
        yield
    finally:
        _synthetic_mode = previous


def _usage_field(usage, name: str) -> int:
    """SDK versiyasiga qarab maydon bo'lmasligi mumkin — 0 ga qaytamiz."""
    value = getattr(usage, name, None)
    return int(value) if isinstance(value, int) else 0


def extract_usage(response) -> dict:
    """Anthropic javobidan token o'lchovlarini oladi."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": _usage_field(usage, "input_tokens"),
        "output_tokens": _usage_field(usage, "output_tokens"),
        "cache_write_tokens": _usage_field(usage, "cache_creation_input_tokens"),
        "cache_read_tokens": _usage_field(usage, "cache_read_input_tokens"),
    }


async def _write(
    agent: str, model: str, tier: str, latency_ms: int, tokens: dict,
    error: str | None, synthetic: bool = False,
) -> None:
    from app.database.models import AgentLog
    from app.database.session import AsyncSessionLocal

    cost = estimate_cost_usd(model, **tokens) if tokens else None
    total_tokens = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)

    extra = {
        "agent": agent,
        "model": model,
        "tier": tier,
        "latency_ms": latency_ms,
        "cost_usd": cost,
        **tokens,
    }
    if synthetic:
        extra["synthetic"] = True
    if error:
        extra["error"] = error[:500]

    message = (
        f"{agent} · {model} · {latency_ms}ms · {total_tokens} token"
        + (f" · ${cost:.5f}" if cost is not None else "")
    )

    async with AsyncSessionLocal() as db:
        db.add(AgentLog(
            level="ERROR" if error else "INFO",
            component=f"{SYNTHETIC_PREFIX if synthetic else COMPONENT_PREFIX}.{agent}",
            message=message if not error else f"{message} — XATO",
            extra=extra,
        ))
        await db.commit()


def record(
    agent: str,
    model: str,
    tier: str,
    latency_ms: int,
    tokens: dict | None = None,
    error: str | None = None,
) -> None:
    """Chaqiruv hisobini fon vazifasi sifatida yozadi (hech qachon ko'tarilmaydi)."""
    # Bayroq CHAQIRUV paytida o'qiladi — fon vazifasi ishga tushganda
    # `synthetic_run()` bloki allaqachon tugagan bo'lishi mumkin.
    synthetic = _synthetic_mode

    async def _safe() -> None:
        try:
            await _write(agent, model, tier, latency_ms, tokens or {}, error, synthetic)
        except Exception as e:  # hisob yuritish asosiy oqimni buzmasin
            logger.debug(f"LLM hisobini yozishda xato: {e}")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # ishlayotgan loop yo'q (test yoki sinxron kontekst)
    asyncio.create_task(_safe())

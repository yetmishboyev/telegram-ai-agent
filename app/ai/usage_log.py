"""LLM chaqiruvlari hisobi — token, narx, kechikish.

`AgentLog` jadvali loyihaning boshidan beri mavjud edi, lekin unga hech kim
yozmasdi. Endi har LLM chaqiruvi shu yerga tushadi: qaysi agent, qaysi model,
qancha token, qancha pul, necha millisekund. Migratsiya kerak emas —
o'lchovlar `extra` JSON ustunida yashaydi.

Yozish har doim fon vazifasida va har doim try/except ichida: hisob yuritish
javob berishni hech qachon sekinlashtirmasligi yoki buzmasligi kerak.
"""
import asyncio

from loguru import logger

from app.ai.models import estimate_cost_usd

COMPONENT_PREFIX = "llm"


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
    error: str | None, extra_fields: dict | None = None,
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
        **(extra_fields or {}),
    }
    if error:
        extra["error"] = error[:500]

    message = (
        f"{agent} · {model} · {latency_ms}ms · {total_tokens} token"
        + (f" · ${cost:.5f}" if cost is not None else "")
    )

    async with AsyncSessionLocal() as db:
        db.add(AgentLog(
            level="ERROR" if error else "INFO",
            component=f"{COMPONENT_PREFIX}.{agent}",
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
    extra: dict | None = None,
) -> None:
    """Chaqiruv hisobini fon vazifasi sifatida yozadi (hech qachon ko'tarilmaydi).

    `extra` — LLM bo'lmagan chaqiruvlar uchun qo'shimcha o'lchovlar
    (masalan transkripsiyada ovoz uzunligi).
    """

    async def _safe() -> None:
        try:
            await _write(agent, model, tier, latency_ms, tokens or {}, error, extra)
        except Exception as e:  # hisob yuritish asosiy oqimni buzmasin
            logger.debug(f"LLM hisobini yozishda xato: {e}")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # ishlayotgan loop yo'q (test yoki sinxron kontekst)
    asyncio.create_task(_safe())

"""LLM xarajati va kechikishi — `AgentLog` ustidagi hisobotlar.

Yozuvlar `app/ai/usage_log.py` tomonidan qo'yiladi. Ikki oqim ajratilgan:
  * `llm.*`    — haqiqiy trafik
  * `replay.*` — `scripts/replay_agents.py` o'lchov yurgizishi

Standart holatda faqat haqiqiy trafik sanaladi; `source=replay` bilan o'lchov
yurgizishlari ko'rsatiladi. Ular aralashtirilmaydi — aks holda "kunlik
xarajat" ma'nosini yo'qotardi.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.usage_log import COMPONENT_PREFIX, SYNTHETIC_PREFIX
from app.api.dependencies import get_current_admin
from app.database.models import AdminUser, AgentLog
from app.database.session import get_db

router = APIRouter(prefix="/costs", tags=["costs"])

MAX_DAYS = 180


def _prefix(source: str) -> str:
    return f"{SYNTHETIC_PREFIX}." if source == "replay" else f"{COMPONENT_PREFIX}."


def _scope(source: str):
    """Manba bo'yicha filtr — haqiqiy trafik va replay hech qachon qo'shilmaydi."""
    return AgentLog.component.startswith(_prefix(source))


# `extra` — generic `JSON` ustuni (JSONB emas), shuning uchun `.astext`
# MAVJUD EMAS. SQLAlchemy ning turga bog'liq aksessorlari ishlatiladi:
# `.as_float()` → CAST(extra ->> 'field' AS FLOAT).
def _num(field: str):
    return AgentLog.extra[field].as_float()


def _text(field: str):
    return AgentLog.extra[field].as_string()


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, min(days, MAX_DAYS)))


@router.get("/summary")
async def get_summary(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    source: str = Query("live", pattern="^(live|replay)$"),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Umumiy ko'rsatkichlar: chaqiruvlar, xarajat, token, kechikish, xatolar."""
    since = _since(days)
    base = [_scope(source), AgentLog.created_at >= since]

    result = await db.execute(
        select(
            func.count(AgentLog.id),
            func.sum(_num("cost_usd")),
            func.sum(_num("input_tokens")),
            func.sum(_num("output_tokens")),
            func.sum(_num("cache_read_tokens")),
            func.avg(_num("latency_ms")),
        ).where(*base)
    )
    calls, cost, tokens_in, tokens_out, cache_read, latency = result.one()

    errors_result = await db.execute(
        select(func.count(AgentLog.id)).where(*base, AgentLog.level == "ERROR")
    )
    errors = errors_result.scalar() or 0

    calls = calls or 0
    cost = float(cost or 0)
    days_span = max(1, min(days, MAX_DAYS))

    return {
        "days": days_span,
        "source": source,
        "calls": calls,
        "errors": errors,
        "error_rate": round(errors / calls * 100, 1) if calls else 0.0,
        "cost_usd": round(cost, 4),
        "cost_per_day": round(cost / days_span, 4),
        "cost_per_call": round(cost / calls, 6) if calls else 0.0,
        "input_tokens": int(tokens_in or 0),
        "output_tokens": int(tokens_out or 0),
        "cache_read_tokens": int(cache_read or 0),
        "avg_latency_ms": int(latency or 0),
    }


@router.get("/by-agent")
async def get_by_agent(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    source: str = Query("live", pattern="^(live|replay)$"),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Agent bo'yicha taqsimot — qaysi biri pulni yeyapti."""
    result = await db.execute(
        select(
            _text("agent").label("agent"),
            _text("model").label("model"),
            func.count(AgentLog.id).label("calls"),
            func.sum(_num("cost_usd")).label("cost"),
            func.avg(_num("latency_ms")).label("latency"),
            func.avg(_num("input_tokens")).label("tokens_in"),
            func.avg(_num("output_tokens")).label("tokens_out"),
        )
        .where(_scope(source), AgentLog.created_at >= _since(days))
        .group_by(_text("agent"), _text("model"))
        .order_by(desc(func.sum(_num("cost_usd"))))
    )
    return [
        {
            "agent": r.agent or "?",
            "model": r.model or "?",
            "calls": r.calls,
            "cost_usd": round(float(r.cost or 0), 5),
            "avg_latency_ms": int(r.latency or 0),
            "avg_input_tokens": int(r.tokens_in or 0),
            "avg_output_tokens": int(r.tokens_out or 0),
        }
        for r in result
    ]


@router.get("/timeline")
async def get_timeline(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    source: str = Query("live", pattern="^(live|replay)$"),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Kunlik xarajat va chaqiruvlar — bo'sh kunlar 0 bilan to'ldiriladi."""
    days = max(1, min(days, MAX_DAYS))
    result = await db.execute(
        select(
            func.date(AgentLog.created_at).label("day"),
            func.count(AgentLog.id).label("calls"),
            func.sum(_num("cost_usd")).label("cost"),
        )
        .where(_scope(source), AgentLog.created_at >= _since(days))
        .group_by(func.date(AgentLog.created_at))
        .order_by(func.date(AgentLog.created_at))
    )
    rows = {
        str(r.day): {"calls": r.calls, "cost": round(float(r.cost or 0), 5)}
        for r in result
    }

    series = []
    today = datetime.now(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        d = str(today - timedelta(days=i))
        item = rows.get(d, {"calls": 0, "cost": 0.0})
        series.append({"date": d, **item})
    return {"days": days, "source": source, "series": series}


@router.get("/expensive")
async def get_expensive_calls(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(10, ge=1, le=50),
    source: str = Query("live", pattern="^(live|replay)$"),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Eng qimmat chaqiruvlar — optimallashtirish qayerdan boshlanishini ko'rsatadi."""
    result = await db.execute(
        select(AgentLog)
        .where(_scope(source), AgentLog.created_at >= _since(days))
        .order_by(desc(_num("cost_usd")))
        .limit(limit)
    )
    return [
        {
            "agent": (log.extra or {}).get("agent", "?"),
            "model": (log.extra or {}).get("model", "?"),
            "cost_usd": (log.extra or {}).get("cost_usd"),
            "input_tokens": (log.extra or {}).get("input_tokens", 0),
            "output_tokens": (log.extra or {}).get("output_tokens", 0),
            "latency_ms": (log.extra or {}).get("latency_ms", 0),
            "created_at": log.created_at.isoformat(),
        }
        for log in result.scalars().all()
    ]


@router.get("/cache")
async def get_cache_effectiveness(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Prompt keshlash ishlayaptimi.

    Kesh faqat barqaror prefiks ~1024 tokendan oshsagina yoqiladi. Undan
    kichik bo'lsa API `cache_control` ni jimgina e'tiborsiz qoldiradi —
    hech narsa buzilmaydi, lekin tejam ham bo'lmaydi. Shu endpoint aynan
    shuni ochiq ko'rsatadi.
    """
    result = await db.execute(
        select(
            func.sum(_num("cache_read_tokens")),
            func.sum(_num("cache_write_tokens")),
            func.sum(_num("input_tokens")),
            func.count(AgentLog.id),
        ).where(_scope("live"), AgentLog.created_at >= _since(days))
    )
    cache_read, cache_write, input_tokens, calls = result.one()

    cache_read = int(cache_read or 0)
    cache_write = int(cache_write or 0)
    input_tokens = int(input_tokens or 0)
    total_input = cache_read + input_tokens

    return {
        "days": max(1, min(days, MAX_DAYS)),
        "calls": calls or 0,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "uncached_input_tokens": input_tokens,
        "hit_rate": round(cache_read / total_input * 100, 1) if total_input else 0.0,
        "working": cache_read > 0,
    }

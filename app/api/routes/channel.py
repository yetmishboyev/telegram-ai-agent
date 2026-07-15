import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database.session import get_db
from app.database.models import AdminUser, ChannelPost, SubscriberSnapshot
from app.api.dependencies import get_current_admin

router = APIRouter(prefix="/channel", tags=["channel"])


@router.get("/stats")
async def get_channel_stats(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    total_r = await db.execute(select(func.count(ChannelPost.id)))
    total = total_r.scalar() or 0

    views_r = await db.execute(select(func.sum(ChannelPost.views)))
    total_views = int(views_r.scalar() or 0)

    edu_r = await db.execute(
        select(func.count(ChannelPost.id)).where(ChannelPost.post_type == "educational")
    )
    edu_count = edu_r.scalar() or 0

    news_r = await db.execute(
        select(func.count(ChannelPost.id)).where(ChannelPost.post_type == "news")
    )
    news_count = news_r.scalar() or 0

    avg_views = round(total_views / total, 1) if total else 0

    best_r = await db.execute(
        select(ChannelPost).order_by(desc(ChannelPost.views)).limit(1)
    )
    best = best_r.scalar_one_or_none()

    edu_views_r = await db.execute(
        select(func.avg(ChannelPost.views)).where(ChannelPost.post_type == "educational")
    )
    news_views_r = await db.execute(
        select(func.avg(ChannelPost.views)).where(ChannelPost.post_type == "news")
    )
    edu_avg = round(float(edu_views_r.scalar() or 0), 1)
    news_avg = round(float(news_views_r.scalar() or 0), 1)

    # Joriy obunachi soni (oxirgi snapshot) + engagement rate
    sub_r = await db.execute(
        select(SubscriberSnapshot).order_by(desc(SubscriberSnapshot.taken_at)).limit(1)
    )
    last_snap = sub_r.scalar_one_or_none()
    subscribers = last_snap.count if last_snap else None
    engagement_rate = (
        round(avg_views / subscribers * 100, 1)
        if subscribers and subscribers > 0 else None
    )

    return {
        "total_posts": total,
        "total_views": total_views,
        "avg_views": avg_views,
        "subscribers": subscribers,
        "engagement_rate": engagement_rate,
        "educational_count": edu_count,
        "news_count": news_count,
        "educational_avg_views": edu_avg,
        "news_avg_views": news_avg,
        "best_post": {
            "id": best.id,
            "topic": best.topic,
            "post_type": best.post_type,
            "views": best.views,
            "text_preview": best.text_preview[:120],
            "sent_at": best.sent_at.isoformat(),
        } if best else None,
    }


@router.get("/posts")
async def get_channel_posts(
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(
        select(ChannelPost).order_by(desc(ChannelPost.sent_at)).limit(limit)
    )
    posts = result.scalars().all()
    return [
        {
            "id": p.id,
            "telegram_message_id": p.telegram_message_id,
            "post_type": p.post_type,
            "topic": p.topic,
            "text_preview": p.text_preview[:200],
            "views": p.views,
            "sent_at": p.sent_at.isoformat(),
            "views_updated_at": p.views_updated_at.isoformat() if p.views_updated_at else None,
        }
        for p in posts
    ]


@router.post("/refresh-views")
async def refresh_channel_views(_: AdminUser = Depends(get_current_admin)):
    """Ko'rishlarni Telegram'dan qo'lda yangilaydi."""
    from app.services.channel_poster import channel_poster
    asyncio.create_task(channel_poster.refresh_views())
    return {"ok": True, "message": "Ko'rishlar yangilanmoqda..."}


@router.post("/post")
async def trigger_channel_post(
    post_type: str = "educational",
    _: AdminUser = Depends(get_current_admin),
):
    """Kanalga post yuborishni qo'lda ishga tushiradi."""
    from app.services.channel_poster import channel_poster
    if post_type == "educational":
        asyncio.create_task(channel_poster.post_educational())
    elif post_type == "digest":
        asyncio.create_task(channel_poster.post_weekly_digest())
    else:
        asyncio.create_task(channel_poster.post_news())
    return {"ok": True, "type": post_type}


@router.get("/timeline")
async def get_channel_timeline(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Kunlik postlar soni va ko'rishlar yig'indisi — grafik uchun seriya."""
    from datetime import timedelta

    days = max(1, min(days, 180))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.date(ChannelPost.sent_at).label("day"),
            func.count(ChannelPost.id).label("posts"),
            func.sum(ChannelPost.views).label("views"),
        )
        .where(ChannelPost.sent_at >= since)
        .group_by(func.date(ChannelPost.sent_at))
        .order_by(func.date(ChannelPost.sent_at))
    )
    rows = {str(r.day): {"posts": r.posts, "views": int(r.views or 0)} for r in result}

    # Bo'sh kunlarni 0 bilan to'ldiramiz — grafikda uzluksiz o'q uchun
    series = []
    today = datetime.now(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        d = str(today - timedelta(days=i))
        item = rows.get(d, {"posts": 0, "views": 0})
        series.append({"date": d, "posts": item["posts"], "views": item["views"]})
    return {"days": days, "series": series}


@router.get("/type-performance")
async def get_type_performance(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Post turlari bo'yicha o'rtacha/jami ko'rishlar — solishtirish grafigi uchun."""
    result = await db.execute(
        select(
            ChannelPost.post_type,
            func.count(ChannelPost.id).label("posts"),
            func.avg(ChannelPost.views).label("avg_views"),
            func.sum(ChannelPost.views).label("total_views"),
        )
        .group_by(ChannelPost.post_type)
        .order_by(desc(func.avg(ChannelPost.views)))
    )
    return [
        {
            "post_type": r.post_type,
            "posts": r.posts,
            "avg_views": round(float(r.avg_views or 0), 1),
            "total_views": int(r.total_views or 0),
        }
        for r in result
    ]


@router.get("/subscribers")
async def get_subscriber_series(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """Obunachilar dinamikasi: kunlik seriya (kunning oxirgi snapshot'i) + o'sish."""
    from datetime import timedelta

    days = max(1, min(days, 180))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(SubscriberSnapshot)
        .where(SubscriberSnapshot.taken_at >= since)
        .order_by(SubscriberSnapshot.taken_at)
    )
    snaps = list(result.scalars().all())

    # Har kun uchun oxirgi qiymat
    by_day: dict[str, int] = {}
    for s in snaps:
        by_day[str(s.taken_at.date())] = s.count

    series = [{"date": d, "count": c} for d, c in sorted(by_day.items())]
    current = snaps[-1].count if snaps else None
    first = snaps[0].count if snaps else None
    growth = (current - first) if (current is not None and first is not None) else 0

    return {
        "days": days,
        "series": series,
        "current": current,
        "growth": growth,
    }


@router.post("/subscribers/snapshot")
async def trigger_subscriber_snapshot(_: AdminUser = Depends(get_current_admin)):
    """Obunachi sonini hozir olish (qo'lda)."""
    from app.services.channel_poster import channel_poster
    count = await channel_poster.snapshot_subscribers()
    return {"ok": count is not None, "count": count}


STRATEGY_CACHE_KEY = "channel_strategy"
STRATEGY_TTL = 86400  # 24 soat


async def _collect_strategy_stats(db: AsyncSession) -> dict:
    """Strategiya uchun ixcham statistika to'plami."""
    stats = await get_channel_stats(db=db, _=None)  # type: ignore[arg-type]
    perf = await get_type_performance(db=db, _=None)  # type: ignore[arg-type]
    tl = await get_channel_timeline(days=30, db=db, _=None)  # type: ignore[arg-type]
    recent_days = [d for d in tl["series"] if d["posts"] > 0][-14:]
    subs = await get_subscriber_series(days=30, db=db, _=None)  # type: ignore[arg-type]
    return {
        "umumiy": {k: v for k, v in stats.items() if k != "best_post"},
        "eng_yaxshi_post": stats.get("best_post"),
        "tur_boyicha": perf,
        "oxirgi_faol_kunlar": recent_days,
        "obunachilar": {
            "joriy": subs["current"],
            "30_kunlik_osish": subs["growth"],
            "dinamika": subs["series"][-14:],
        },
    }


@router.get("/strategy")
async def get_growth_strategy(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """AI o'sish strategiyasi — 24 soat keshlanadi."""
    import json as _json
    from app.database.redis import get_redis

    r = await get_redis()
    cached = await r.get(STRATEGY_CACHE_KEY)
    if cached:
        return {"cached": True, **_json.loads(cached)}

    from app.services.news_fetcher import news_fetcher
    stats = await _collect_strategy_stats(db)
    strategy = await news_fetcher.generate_growth_strategy(stats)
    if not strategy:
        return {"cached": False, "holat": None}

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), **strategy}
    await r.setex(STRATEGY_CACHE_KEY, STRATEGY_TTL, _json.dumps(payload, ensure_ascii=False))
    return {"cached": False, **payload}


@router.post("/strategy/refresh")
async def refresh_growth_strategy(_: AdminUser = Depends(get_current_admin)):
    """Keshni tozalaydi — keyingi GET yangi strategiya generatsiya qiladi."""
    from app.database.redis import get_redis
    r = await get_redis()
    await r.delete(STRATEGY_CACHE_KEY)
    return {"ok": True}

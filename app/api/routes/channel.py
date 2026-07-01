import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database.session import get_db
from app.database.models import AdminUser, ChannelPost
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

    return {
        "total_posts": total,
        "total_views": total_views,
        "avg_views": avg_views,
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

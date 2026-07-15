"""Obunachi statistikasi kuzatuvi testlari."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.channel import get_subscriber_series, get_channel_stats
from app.database.models import SubscriberSnapshot
from app.services.channel_poster import channel_poster


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return _FakeResp(self._payload)


@pytest.mark.asyncio
async def test_snapshot_subscribers_saves_count(db_session):
    with patch("httpx.AsyncClient", lambda *a, **k: _FakeClient({"ok": True, "result": 128})):
        count = await channel_poster.snapshot_subscribers()
    assert count == 128
    # DB'da yozildi — tozalaymiz
    from sqlalchemy import select, delete
    r = await db_session.execute(
        select(SubscriberSnapshot).order_by(SubscriberSnapshot.id.desc()).limit(1)
    )
    snap = r.scalar_one()
    assert snap.count == 128
    await db_session.execute(delete(SubscriberSnapshot).where(SubscriberSnapshot.id == snap.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_snapshot_returns_none_on_api_error():
    with patch("httpx.AsyncClient", lambda *a, **k: _FakeClient({"ok": False, "description": "forbidden"})):
        count = await channel_poster.snapshot_subscribers()
    assert count is None


@pytest.mark.asyncio
async def test_subscriber_series_daily_last_value_and_growth(db_session):
    now = datetime.now(timezone.utc)
    rows = [
        SubscriberSnapshot(count=100, taken_at=now - timedelta(days=2, hours=3)),
        SubscriberSnapshot(count=105, taken_at=now - timedelta(days=2, hours=1)),  # shu kun oxirgisi
        SubscriberSnapshot(count=110, taken_at=now - timedelta(days=1)),
        SubscriberSnapshot(count=120, taken_at=now),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    try:
        data = await get_subscriber_series(days=7, db=db_session, _=None)
        assert data["current"] == 120
        assert data["growth"] == 20  # 120 - 100
        by_date = {s["date"]: s["count"] for s in data["series"]}
        # 2 kun avvalgi kun uchun oxirgi qiymat (105) olinadi
        assert 105 in by_date.values()
        assert 100 not in by_date.values()
    finally:
        from sqlalchemy import delete
        await db_session.execute(
            delete(SubscriberSnapshot).where(
                SubscriberSnapshot.id.in_([r.id for r in rows])
            )
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_stats_include_engagement_rate(db_session):
    snap = SubscriberSnapshot(count=200, taken_at=datetime.now(timezone.utc))
    db_session.add(snap)
    await db_session.commit()
    try:
        stats = await get_channel_stats(db=db_session, _=None)
        assert stats["subscribers"] == 200
        if stats["avg_views"]:
            assert stats["engagement_rate"] == round(stats["avg_views"] / 200 * 100, 1)
    finally:
        await db_session.delete(snap)
        await db_session.commit()

"""API testlari"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_endpoint():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_login_wrong_credentials():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/token",
            data={"username": "wrong", "password": "wrong"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_without_token():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/chats/")
    assert resp.status_code == 401

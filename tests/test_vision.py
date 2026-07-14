"""Vision agenti testlari — anthropic client mock qilinadi (haqiqiy API chaqiruvsiz)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.agents.vision_agent import vision_agent


def _fake_response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.mark.asyncio
async def test_vision_describe_returns_text():
    fake = _fake_response("Rasmda hujjat va 'SHARTNOMA' matni bor.")
    with patch.object(vision_agent._client.messages, "create", AsyncMock(return_value=fake)):
        r = await vision_agent.describe(b"\xff\xd8\xff\xe0", media_type="image/jpeg")
    assert r == "Rasmda hujjat va 'SHARTNOMA' matni bor."


@pytest.mark.asyncio
async def test_vision_describe_normalizes_unsupported_mime():
    fake = _fake_response("ok")
    with patch.object(vision_agent._client.messages, "create", AsyncMock(return_value=fake)) as m:
        await vision_agent.describe(b"x", media_type="image/tiff")  # ruxsat etilmagan
    source = m.await_args.kwargs["messages"][0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_vision_describe_returns_none_on_error():
    with patch.object(
        vision_agent._client.messages, "create", AsyncMock(side_effect=RuntimeError("api"))
    ):
        r = await vision_agent.describe(b"x", media_type="image/jpeg")
    assert r is None


@pytest.mark.asyncio
async def test_vision_describe_none_on_empty_text():
    with patch.object(vision_agent._client.messages, "create", AsyncMock(return_value=_fake_response("   "))):
        r = await vision_agent.describe(b"x", media_type="image/jpeg")
    assert r is None

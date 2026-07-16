"""Self-critique sifat qatlami testlari."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher
from app.services.channel_poster import channel_poster

_DRAFT = "**Sarlavha**\n\nBu qoralama post matni. Yetarlicha uzun bo'lishi uchun yana bir gap." * 2


@pytest.mark.asyncio
async def test_critique_returns_improved_text():
    improved = _DRAFT + " Endi yaxshilangan yakun va CTA bor."
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value=improved)):
        result = await news_fetcher.critique_and_improve(_DRAFT, "educational")
    assert result == improved


@pytest.mark.asyncio
async def test_critique_falls_back_on_error():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(side_effect=RuntimeError("down"))):
        result = await news_fetcher.critique_and_improve(_DRAFT)
    assert result == _DRAFT


@pytest.mark.asyncio
async def test_critique_rejects_suspiciously_short_result():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value="Qisqa.")):
        result = await news_fetcher.critique_and_improve(_DRAFT)
    assert result == _DRAFT  # 50% dan qisqa natija rad etiladi


@pytest.mark.asyncio
async def test_send_for_approval_runs_critique():
    """Barcha post yo'llari _send_for_approval orqali critique'dan o'tadi."""
    with patch("app.services.news_fetcher.news_fetcher.critique_and_improve",
               AsyncMock(return_value="TAHRIRLANGAN POST")) as crit, \
         patch("app.services.bot_service.bot_service._client") as client, \
         patch("app.database.redis.get_redis", AsyncMock(return_value=AsyncMock())):
        client.send_message = AsyncMock()
        await channel_poster._send_for_approval("QORALAMA", "educational", "mavzu", "chapani")

    crit.assert_awaited_once_with("QORALAMA", "educational")
    # Egaga yuborilgan xabarda tahrirlangan matn bor
    sent_text = client.send_message.await_args.args[1]
    assert "TAHRIRLANGAN POST" in sent_text
    assert "QORALAMA" not in sent_text

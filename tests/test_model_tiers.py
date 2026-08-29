"""Model qatlamlari, effort tarjimasi va narx hisobi (Faza 00).

Eng muhim tekshiruv: yangi avlod modellariga `temperature` YUBORILMASLIGI
kerak — API uni 400 bilan rad etadi, ya'ni bitta e'tiborsizlik butun agentni
o'chirib qo'yadi.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.models import (
    ModelTier, effort_for_temperature, estimate_cost_usd, uses_effort,
)
from app.ai.agents.base_agent import BaseAgent
from app.config import settings


# ─── imkoniyat aniqlash ────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [
    "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-fable-5",
])
def test_new_generation_uses_effort(model):
    assert uses_effort(model) is True


@pytest.mark.parametrize("model", [
    "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-6", "gpt-4o",
])
def test_older_models_keep_temperature(model):
    assert uses_effort(model) is False


@pytest.mark.parametrize("temperature,expected", [
    (0.0, "low"), (0.1, "low"), (0.3, "low"),
    (0.4, "medium"), (0.6, "medium"),
    (0.7, "high"), (0.9, "high"), (1.0, "high"),
])
def test_temperature_translates_to_effort(temperature, expected):
    assert effort_for_temperature(temperature) == expected


@pytest.mark.parametrize("tier,temperature,expected", [
    # Qatlam shifti kechikishni cheklaydi: foydalanuvchi javobini kutib turadi
    ("balanced", 0.9, "medium"),   # high emas — javob tez kelishi kerak
    ("balanced", 0.4, "medium"),
    ("balanced", 0.1, "low"),      # shift pastga majburlamaydi
    ("fast",     0.9, "low"),
    ("deep",     0.9, "high"),     # kanal posti fon vazifasi — cheklanmaydi
    ("deep",     0.3, "low"),
])
def test_tier_caps_effort(tier, temperature, expected):
    assert effort_for_temperature(temperature, tier) == expected


# ─── qatlam → model ────────────────────────────────────────────────────────────

def test_tier_resolves_to_configured_model():
    assert settings.model_for_tier("fast") == settings.anthropic_model_fast
    assert settings.model_for_tier("balanced") == settings.anthropic_model
    assert settings.model_for_tier("deep") == settings.anthropic_model_deep


def test_unknown_tier_falls_back_to_main_model():
    assert settings.model_for_tier("nomalum") == settings.anthropic_model


def test_agents_are_assigned_expected_tiers():
    from app.ai.agents.analysis_agent import analysis_agent
    from app.ai.agents.classifier_agent import classifier_agent
    from app.ai.agents.response_agent import response_agent
    from app.services.news_fetcher import news_fetcher

    assert analysis_agent.tier is ModelTier.FAST
    assert classifier_agent.tier is ModelTier.FAST
    assert response_agent.tier is ModelTier.BALANCED
    assert news_fetcher.tier is ModelTier.DEEP


# ─── chaqiruv shakli ───────────────────────────────────────────────────────────

class _Probe(BaseAgent):
    async def run(self, *a, **kw):
        return None


def _fake_response(text="javob"):
    class Block:
        type = "text"
    block = Block()
    block.text = text

    class Usage:
        input_tokens = 100
        output_tokens = 50
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class Response:
        content = [block]
        usage = Usage()
    return Response()


async def _capture_call(model: str) -> dict:
    """Berilgan model bilan chaqiruv qilib, SDK ga ketgan argumentlarni qaytaradi."""
    agent = _Probe()
    agent._model = model
    create = AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, \
         patch("app.ai.usage_log.record"):
        client.messages.create = create
        await agent._call_llm(messages=[{"role": "user", "content": "salom"}],
                              temperature=0.1, max_tokens=256)
    return create.call_args.kwargs


@pytest.mark.asyncio
async def test_new_model_gets_effort_and_no_temperature():
    kwargs = await _capture_call("claude-sonnet-5")
    assert "temperature" not in kwargs, "temperature yuborilsa API 400 qaytaradi"
    assert kwargs["output_config"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_balanced_agent_never_exceeds_medium_effort():
    """Javob quvuri kechikishga sezgir — `high` u yerga chiqmasligi kerak."""
    agent = _Probe()
    agent._model = "claude-sonnet-5"
    create = AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, patch("app.ai.usage_log.record"):
        client.messages.create = create
        await agent._call_llm(messages=[{"role": "user", "content": "x"}], temperature=0.9)
    assert create.call_args.kwargs["output_config"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_older_model_gets_temperature_and_no_effort():
    kwargs = await _capture_call("claude-haiku-4-5")
    assert kwargs["temperature"] == 0.1
    assert "output_config" not in kwargs, "eski modellar effort'ni tushunmaydi"


@pytest.mark.asyncio
async def test_text_extracted_past_thinking_blocks():
    """Adaptive thinking yoqilganda birinchi blok `thinking` bo'lishi mumkin."""
    class Thinking:
        type = "thinking"
    class Text:
        type = "text"
        text = "haqiqiy javob"

    class Response:
        content = [Thinking(), Text()]
        usage = None

    assert BaseAgent._first_text(Response()) == "haqiqiy javob"


def test_first_text_survives_empty_content():
    class Response:
        content = []
    assert BaseAgent._first_text(Response()) == ""


def test_first_text_falls_back_to_any_block_with_text():
    """`type` maydoni bo'lmagan blok (eski SDK yoki mock) ham o'qilishi kerak."""
    from types import SimpleNamespace
    response = SimpleNamespace(content=[SimpleNamespace(text="matn")])
    assert BaseAgent._first_text(response) == "matn"


def test_first_text_ignores_thinking_block_without_text():
    from types import SimpleNamespace
    response = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="ichki fikr"),
        SimpleNamespace(type="text", text="javob"),
    ])
    assert BaseAgent._first_text(response) == "javob"


# ─── narx ──────────────────────────────────────────────────────────────────────

def test_cost_matches_published_rates():
    # Sonnet 5: $2 / 1M kiruvchi, $10 / 1M chiquvchi
    cost = estimate_cost_usd("claude-sonnet-5", input_tokens=1_000_000)
    assert cost == pytest.approx(2.0)
    cost = estimate_cost_usd("claude-sonnet-5", output_tokens=1_000_000)
    assert cost == pytest.approx(10.0)


def test_cache_read_is_cheaper_than_fresh_input():
    fresh = estimate_cost_usd("claude-opus-5", input_tokens=100_000)
    cached = estimate_cost_usd("claude-opus-5", cache_read_tokens=100_000)
    assert cached == pytest.approx(fresh * 0.10)


def test_unknown_model_returns_none_instead_of_guessing():
    assert estimate_cost_usd("qandaydir-model", input_tokens=1000) is None

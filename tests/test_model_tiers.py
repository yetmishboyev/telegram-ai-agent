"""Model qatlamlari, effort tarjimasi va narx hisobi (Faza 00).

Eng muhim tekshiruv: yangi avlod modellariga `temperature` YUBORILMASLIGI
kerak — API uni 400 bilan rad etadi, ya'ni bitta e'tiborsizlik butun agentni
o'chirib qo'yadi.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.models import (
    MIN_OUTPUT_TOKENS_WITH_THINKING, ModelTier, SDK_ACCEPTS_TEMPERATURE,
    effort_for_temperature, estimate_cost_usd, min_output_tokens,
    sampling_mode, uses_effort,
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
    "claude-haiku-4-5", "claude-sonnet-4-5", "gpt-4o",
])
def test_models_without_effort_support(model):
    assert uses_effort(model) is False


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-6"])
def test_4_6_family_also_supports_effort(model):
    """4.6-oila `effort` ni tushunadi (low/medium/high/max)."""
    assert uses_effort(model) is True


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
    with patch.object(settings, "anthropic_model", "asosiy"), \
         patch.object(settings, "anthropic_model_fast", "tez"), \
         patch.object(settings, "anthropic_model_deep", "chuqur"):
        assert settings.model_for_tier("fast") == "tez"
        assert settings.model_for_tier("balanced") == "asosiy"
        assert settings.model_for_tier("deep") == "chuqur"


def test_empty_tier_falls_back_to_main_model():
    """Faqat ANTHROPIC_MODEL sozlangan o'rnatma deploydan keyin o'zgarmasin."""
    with patch.object(settings, "anthropic_model", "asosiy"), \
         patch.object(settings, "anthropic_model_fast", ""), \
         patch.object(settings, "anthropic_model_deep", ""):
        assert settings.model_for_tier("fast") == "asosiy"
        assert settings.model_for_tier("deep") == "asosiy"


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
async def test_effortless_model_never_gets_effort():
    kwargs = await _capture_call("claude-haiku-4-5")
    assert "output_config" not in kwargs, "Haiku 4.5 effort'ni rad etadi"
    # `temperature` faqat SDK uni qabul qilsa yuboriladi
    assert ("temperature" in kwargs) is SDK_ACCEPTS_TEMPERATURE


def test_sampling_mode_respects_both_model_and_sdk():
    """Model YETARLI EMAS — SDK imkoniyati ham hisobga olinishi kerak.

    SDK 1.x da `temperature` butunlay yo'q: eski model uchun uni yuborish
    TypeError beradi va har chaqiruv yiqiladi (2026-08-30, produksiya).
    """
    assert sampling_mode("claude-sonnet-5") == "effort"
    assert sampling_mode("claude-sonnet-4-6") == "effort"
    expected = "temperature" if SDK_ACCEPTS_TEMPERATURE else "none"
    assert sampling_mode("claude-haiku-4-5") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [
    "claude-opus-5", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-haiku-4-5", "claude-sonnet-4-5",
])
async def test_every_argument_is_accepted_by_the_real_sdk(model):
    """Qurilgan argumentlar HAQIQIY SDK imzosiga mos kelishi shart.

    Mavjud testlar `AsyncMock` ishlatadi — u istalgan argumentni qabul
    qiladi, shuning uchun yaroqsiz argument ular orqali sezilmaydi. Bu test
    argumentlarni SDK ning haqiqiy imzosiga solishtiradi (API chaqirmasdan).
    """
    import inspect
    from anthropic.resources.messages import AsyncMessages

    kwargs = await _capture_call(model)
    allowed = set(inspect.signature(AsyncMessages.create).parameters)
    unknown = set(kwargs) - allowed
    assert not unknown, f"SDK bilmaydigan argument(lar): {unknown}"


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


# ─── fikrlash byudjeti ─────────────────────────────────────────────────────────

def test_thinking_models_get_a_token_floor():
    assert min_output_tokens("claude-opus-5") == MIN_OUTPUT_TOKENS_WITH_THINKING
    assert min_output_tokens("claude-sonnet-5") == MIN_OUTPUT_TOKENS_WITH_THINKING


def test_models_without_thinking_are_not_raised():
    assert min_output_tokens("claude-haiku-4-5") == 1


@pytest.mark.asyncio
async def test_small_budget_is_raised_for_thinking_models():
    """Past shift bilan model o'ylab tugatadi va MATN QAYTARMAYDI.

    O'lchangan (2026-08-30, claude-opus-5): max_tokens=100 da javob bo'sh
    keldi — xato ham berilmadi, shunchaki matn bloki yo'q edi.
    """
    agent = _Probe()
    agent._model = "claude-opus-5"
    create = AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, patch("app.ai.usage_log.record"):
        client.messages.create = create
        await agent._call_llm(messages=[{"role": "user", "content": "x"}], max_tokens=100)

    assert create.call_args.kwargs["max_tokens"] == MIN_OUTPUT_TOKENS_WITH_THINKING


@pytest.mark.asyncio
async def test_generous_budget_is_left_alone():
    agent = _Probe()
    agent._model = "claude-opus-5"
    create = AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, patch("app.ai.usage_log.record"):
        client.messages.create = create
        await agent._call_llm(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=MIN_OUTPUT_TOKENS_WITH_THINKING + 4000,
        )

    # Shiftdan yuqori byudjetga tegilmaydi (shift o'zgarsa test ham ergashadi)
    assert (create.call_args.kwargs["max_tokens"]
            == MIN_OUTPUT_TOKENS_WITH_THINKING + 4000)


@pytest.mark.asyncio
async def test_haiku_keeps_its_small_budget():
    """Haiku 4.5 da fikrlash yo'q — byudjetni ko'tarish bekorga token sarflashi mumkin."""
    agent = _Probe()
    agent._model = "claude-haiku-4-5"
    create = AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, patch("app.ai.usage_log.record"):
        client.messages.create = create
        await agent._call_llm(messages=[{"role": "user", "content": "x"}], max_tokens=256)

    assert create.call_args.kwargs["max_tokens"] == 256

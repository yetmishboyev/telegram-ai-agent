"""Xarajat hisoboti va sun'iy qatorlarni ajratish.

Eng muhim shart: replay yurgizishlari haqiqiy trafik hisobiga ARALASHMASLIGI
kerak. Aks holda "kunlik xarajat" ma'nosini yo'qotadi — qatorlarning yarmi
foydalanuvchidan emas, o'lchov skriptidan kelgan bo'lardi.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.ai import usage_log


# ─── sun'iy belgilash ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_flag():
    usage_log._synthetic_mode = False
    yield
    usage_log._synthetic_mode = False


def _captured_write():
    """`_write` chaqiruv argumentlarini ushlaydi."""
    return patch.object(usage_log, "_write", AsyncMock())


@pytest.mark.asyncio
async def test_live_calls_are_not_marked_synthetic():
    with _captured_write() as write:
        usage_log.record(agent="A", model="m", tier="fast", latency_ms=10)
        await _drain()
    assert write.await_args.args[-1] is False


@pytest.mark.asyncio
async def test_calls_inside_synthetic_run_are_marked():
    with _captured_write() as write:
        with usage_log.synthetic_run():
            usage_log.record(agent="A", model="m", tier="fast", latency_ms=10)
        await _drain()
    assert write.await_args.args[-1] is True


@pytest.mark.asyncio
async def test_flag_is_read_at_call_time_not_when_the_task_runs():
    """Fon vazifasi blok tugagandan keyin ishga tushishi mumkin."""
    with _captured_write() as write:
        with usage_log.synthetic_run():
            usage_log.record(agent="A", model="m", tier="fast", latency_ms=10)
        # blok tugadi — endi fon vazifasi ishlaydi
        await _drain()
    assert write.await_args.args[-1] is True, "bayroq chaqiruv paytida o'qilishi kerak"


@pytest.mark.asyncio
async def test_synthetic_run_restores_previous_state():
    with usage_log.synthetic_run():
        assert usage_log._synthetic_mode is True
    assert usage_log._synthetic_mode is False


@pytest.mark.asyncio
async def test_synthetic_run_restores_even_on_error():
    with pytest.raises(RuntimeError):
        with usage_log.synthetic_run():
            raise RuntimeError("xato")
    assert usage_log._synthetic_mode is False


async def _drain():
    """Fon vazifalari tugashini kutadi."""
    import asyncio
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ─── komponent prefiksi ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("synthetic,expected", [
    (False, "llm.TestAgent"),
    (True, "replay.TestAgent"),
])
async def test_component_prefix_separates_the_two_streams(synthetic, expected):
    added = []

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        def add(self, obj): added.append(obj)
        async def commit(self): pass

    with patch("app.database.session.AsyncSessionLocal", _Session), \
         patch("app.database.models.AgentLog") as log_cls:
        log_cls.side_effect = lambda **kw: kw
        await usage_log._write(
            agent="TestAgent", model="claude-haiku-4-5", tier="fast",
            latency_ms=100, tokens={"input_tokens": 10, "output_tokens": 5},
            error=None, synthetic=synthetic,
        )

    assert added[0]["component"] == expected
    assert added[0]["extra"].get("synthetic") is (True if synthetic else None)


# ─── replay skripti ────────────────────────────────────────────────────────────

def test_replay_filters_placeholders_and_short_text():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "replay_agents", Path(__file__).parent.parent / "scripts" / "replay_agents.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # O'rinbosarlar haqiqiy xabar emas — o'lchovga kirmasligi kerak
    for placeholder in module._PLACEHOLDERS:
        assert any(placeholder.startswith(p) for p in module._PLACEHOLDERS)
    assert module.MIN_LENGTH >= 10
    assert module.CONCURRENCY <= 8, "API ni bo'g'ib qo'ymaslik uchun"


def test_replay_never_imports_process_message():
    """Skript quvurni chaqirmasligi SHART — u xabar yuboradi va DB ga yozadi."""
    from pathlib import Path
    source = (Path(__file__).parent.parent / "scripts" / "replay_agents.py").read_text()
    assert "process_message" not in source.split('"""', 2)[2], \
        "replay skripti ai_service.process_message ni chaqirmasligi kerak"

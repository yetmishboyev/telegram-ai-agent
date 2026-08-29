"""Strukturali chiqish va prompt keshlash (Faza 00, 2-qism).

Sxema berilganda API javob shaklini kafolatlaydi va `json_parse.py` dagi
"model JSONdan keyin izoh yozdi" turidagi xatolar sinfi yo'qoladi. Lekin
sxema qabul qilinmasa tizim ishlashdan to'xtamasligi kerak — zaxira yo'l
shu fayldagi eng muhim tekshiruv.
"""
from unittest.mock import AsyncMock, patch

import pytest

import app.ai.agents.base_agent as base_agent_module
from app.ai.agents.base_agent import BaseAgent
from app.ai.prompts.system_prompt import (
    AGENT_PERSONA, build_system_blocks, build_system_prompt,
)
from app.ai import schemas


class _Probe(BaseAgent):
    async def run(self, *a, **kw):
        return None


def _fake_response(text='{"ok": true}'):
    class Block:
        type = "text"
    block = Block()
    block.text = text

    class Usage:
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class Response:
        content = [block]
        usage = Usage()
    return Response()


@pytest.fixture(autouse=True)
def _reset_capability():
    """Modul darajasidagi bayroq testlar orasida tiklanadi."""
    base_agent_module._structured_output_ok = True
    yield
    base_agent_module._structured_output_ok = True


async def _call(schema=None, model="claude-sonnet-5", create=None):
    agent = _Probe()
    agent._model = model
    create = create or AsyncMock(return_value=_fake_response())
    with patch.object(agent, "_client") as client, patch("app.ai.usage_log.record"):
        client.messages.create = create
        text = await agent._call_llm(
            messages=[{"role": "user", "content": "x"}],
            temperature=0.1, response_schema=schema,
        )
    return text, create


# ─── sxema uzatilishi ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_is_sent_as_json_schema_format():
    _, create = await _call(schemas.CLASSIFICATION_SCHEMA)
    output_config = create.call_args.kwargs["output_config"]
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"] is schemas.CLASSIFICATION_SCHEMA
    # effort va format bitta output_config ichida yashaydi
    assert output_config["effort"] == "low"


@pytest.mark.asyncio
async def test_no_schema_means_no_format_key():
    _, create = await _call(None)
    assert "format" not in create.call_args.kwargs.get("output_config", {})


# ─── zaxira yo'l ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_falls_back_to_plain_call_when_schema_rejected():
    """Sxema rad etilsa chaqiruv sxemasiz takrorlanadi — javob yo'qolmaydi."""
    calls: list[dict] = []

    async def flaky(**kwargs):
        calls.append(kwargs)
        if "format" in kwargs.get("output_config", {}):
            raise RuntimeError("400 output_config.format qo'llab-quvvatlanmaydi")
        return _fake_response('{"ok": true}')

    text, _ = await _call(schemas.QUIZ_SCHEMA, create=AsyncMock(side_effect=flaky))

    assert text == '{"ok": true}'
    assert len(calls) == 2
    assert "format" in calls[0]["output_config"]
    assert "format" not in calls[1].get("output_config", {})


@pytest.mark.asyncio
async def test_capability_is_disabled_after_first_rejection():
    """Ikkinchi chaqiruv sxemani umuman yubormaydi — har safar 2 so'rov ketmasin."""
    async def flaky(**kwargs):
        if "format" in kwargs.get("output_config", {}):
            raise RuntimeError("400")
        return _fake_response()

    await _call(schemas.QUIZ_SCHEMA, create=AsyncMock(side_effect=flaky))
    assert base_agent_module._structured_output_enabled() is False

    _, create = await _call(schemas.QUIZ_SCHEMA)
    assert "format" not in create.call_args.kwargs.get("output_config", {})
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_error_without_schema_is_not_retried():
    """Sxemasiz chaqiruvdagi xato zaxira yo'lga tushmaydi — u haqiqiy xato."""
    create = AsyncMock(side_effect=RuntimeError("tarmoq uzildi"))
    with pytest.raises(RuntimeError):
        await _call(None, create=create)
    assert create.await_count == 1


# ─── sxemalarning o'zi ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("schema", [
    schemas.MESSAGE_ANALYSIS_SCHEMA, schemas.CLASSIFICATION_SCHEMA,
    schemas.EXTRACTED_FACTS_SCHEMA, schemas.CURATION_SCHEMA,
    schemas.QUIZ_SCHEMA, schemas.POLL_SCHEMA, schemas.GROWTH_STRATEGY_SCHEMA,
])
def test_schemas_are_well_formed(schema):
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    # Har talab qilingan maydon `properties` da ta'riflangan bo'lishi shart
    assert set(schema["required"]) <= set(schema["properties"])


def test_classification_schema_matches_the_pydantic_model():
    """Sxema va model bir-biridan ajralib ketmasligi kerak."""
    from app.ai.agents.classifier_agent import ClassificationResult, MessageCategory

    assert set(schemas.CLASSIFICATION_SCHEMA["properties"]) == set(
        ClassificationResult.model_fields
    )
    assert set(schemas.CLASSIFICATION_SCHEMA["properties"]["category"]["enum"]) == {
        c.value for c in MessageCategory
    }


def test_analysis_schema_matches_the_pydantic_model():
    from app.ai.agents.analysis_agent import MessageAnalysis

    assert set(schemas.MESSAGE_ANALYSIS_SCHEMA["properties"]) == set(
        MessageAnalysis.model_fields
    )


# ─── prompt keshlash ───────────────────────────────────────────────────────────

def test_stable_block_carries_the_cache_breakpoint():
    blocks = build_system_blocks(relationship_type="stranger", schedule_context="jadval")
    assert len(blocks) == 2
    assert blocks[0]["text"] == AGENT_PERSONA
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1], "o'zgaruvchan qism keshlanmasligi kerak"


def test_variable_content_stays_out_of_the_cached_prefix():
    """Suhbatdoshga bog'liq narsa keshlangan blokka tushsa, kesh har safar bekor bo'lardi."""
    blocks = build_system_blocks(schedule_context="soat 10 da yig'ilish")
    assert "yig'ilish" not in blocks[0]["text"]
    assert "yig'ilish" in blocks[1]["text"]


def test_blocks_and_flat_prompt_carry_the_same_text():
    kwargs = dict(relationship_type="colleague", schedule_context="jadval",
                  style_examples="uslub", conversation_summary="xulosa")
    flat = build_system_prompt(**kwargs)
    blocks = build_system_blocks(**kwargs)
    assert flat == blocks[0]["text"] + blocks[1]["text"]


def test_openai_path_flattens_system_blocks():
    blocks = build_system_blocks(relationship_type="friend")
    flattened = BaseAgent._flatten_system(blocks)
    assert AGENT_PERSONA in flattened
    assert "do'sti" in flattened


def test_flatten_passes_plain_string_through():
    assert BaseAgent._flatten_system("oddiy matn") == "oddiy matn"
    assert BaseAgent._flatten_system(None) is None

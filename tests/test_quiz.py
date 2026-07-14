"""Interaktiv quiz/so'rovnoma testlari."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.news_fetcher import news_fetcher
from app.services.channel_poster import channel_poster


def _llm(return_value):
    return patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(return_value=return_value))


@pytest.mark.asyncio
async def test_generate_quiz_parses_and_validates():
    fake = json.dumps({
        "question": "RAG nima?", "options": ["A", "B", "C", "D"],
        "correct_index": 2, "explanation": "chunki shunday",
    })
    with _llm(fake):
        q = await news_fetcher.generate_quiz("RAG", "quiz")
    assert q["kind"] == "quiz"
    assert q["question"] == "RAG nima?"
    assert len(q["options"]) == 4
    assert q["correct_index"] == 2


@pytest.mark.asyncio
async def test_generate_quiz_bad_index_defaults_to_zero():
    with _llm(json.dumps({"question": "?", "options": ["A", "B"], "correct_index": 9})):
        q = await news_fetcher.generate_quiz("x", "quiz")
    assert q["correct_index"] == 0


@pytest.mark.asyncio
async def test_generate_quiz_none_on_too_few_options():
    with _llm(json.dumps({"question": "?", "options": ["bitta"]})):
        q = await news_fetcher.generate_quiz("x", "poll")
    assert q is None


@pytest.mark.asyncio
async def test_poll_kind_has_no_correct_index():
    with _llm(json.dumps({"question": "Sizningcha?", "options": ["Ha", "Yo'q"]})):
        q = await news_fetcher.generate_quiz("x", "poll")
    assert q["kind"] == "poll"
    assert "correct_index" not in q


@pytest.mark.asyncio
async def test_send_poll_builds_correct_bot_api_payload():
    quiz = {"kind": "quiz", "question": "Q?", "options": ["A", "B"],
            "correct_index": 1, "explanation": "izoh"}
    captured = {}

    class FakeResp:
        def json(self):
            return {"ok": True, "result": {"message_id": 55}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["payload"] = json
            return FakeResp()

    with patch("httpx.AsyncClient", lambda *a, **k: FakeClient()):
        mid = await channel_poster._send_poll_to_channel(quiz)

    assert mid == 55
    assert "sendPoll" in captured["url"]
    assert captured["payload"]["type"] == "quiz"
    assert captured["payload"]["correct_option_id"] == 1
    assert captured["payload"]["options"] == [{"text": "A"}, {"text": "B"}]

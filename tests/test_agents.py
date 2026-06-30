"""Agent testlari"""
import pytest
from unittest.mock import AsyncMock, patch
from app.ai.agents.analysis_agent import AnalysisAgent, MessageAnalysis
from app.ai.agents.response_agent import ResponseAgent


class TestAnalysisAgent:
    @pytest.mark.asyncio
    async def test_analyze_normal_message(self):
        agent = AnalysisAgent()
        mock_response = '{"sentiment":"positive","intent":"greeting","importance":0.5,"threat_level":"none","is_spam":false,"is_phishing":false,"is_manipulative":false,"is_toxic":false,"should_respond":true,"response_priority":"medium","detected_language":"uz","confidence":0.95,"reason":"Oddiy salomlashish"}'

        with patch.object(agent, "_call_llm", AsyncMock(return_value=mock_response)):
            result = await agent.analyze_message("Salom, qalaysiz?")

        assert isinstance(result, MessageAnalysis)
        assert result.sentiment == "positive"
        assert result.is_spam is False
        assert result.should_respond is True

    @pytest.mark.asyncio
    async def test_analyze_spam_message(self):
        agent = AnalysisAgent()
        mock_response = '{"sentiment":"neutral","intent":"spam","importance":0.1,"threat_level":"medium","is_spam":true,"is_phishing":false,"is_manipulative":false,"is_toxic":false,"should_respond":false,"response_priority":"low","detected_language":"ru","confidence":0.9,"reason":"Reklama xabari"}'

        with patch.object(agent, "_call_llm", AsyncMock(return_value=mock_response)):
            result = await agent.analyze_message("Сайт: заработай 1000$ в день!")

        assert result.is_spam is True
        assert result.should_respond is False

    @pytest.mark.asyncio
    async def test_analyze_invalid_json_returns_default(self):
        agent = AnalysisAgent()
        with patch.object(agent, "_call_llm", AsyncMock(return_value="yaroqsiz json")):
            result = await agent.analyze_message("test")
        assert isinstance(result, MessageAnalysis)
        assert result.should_respond is True


class TestResponseAgent:
    @pytest.mark.asyncio
    async def test_generate_response(self):
        from app.database.models import TelegramUser
        agent = ResponseAgent()
        user = TelegramUser(
            telegram_id=123456,
            first_name="Jasur",
            relationship_type="friend",
        )

        with patch.object(agent, "_call_llm", AsyncMock(return_value="Ha, qandaysan!")):
            response = await agent.generate_response(
                user=user,
                current_message="Salom!",
                history=[],
            )

        assert response == "Ha, qandaysan!"
        assert len(response) > 0

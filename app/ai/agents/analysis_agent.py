import json
from loguru import logger
from pydantic import BaseModel

from app.ai.agents.base_agent import BaseAgent
from app.ai.models import ModelTier
from app.ai.agents.json_parse import parse_json_response
from app.ai.prompts.system_prompt import ANALYSIS_PROMPT, MEMORY_EXTRACTION_PROMPT
from app.ai.schemas import EXTRACTED_FACTS_SCHEMA, MESSAGE_ANALYSIS_SCHEMA


class MessageAnalysis(BaseModel):
    sentiment: str = "neutral"
    intent: str = "other"
    importance: float = 0.5
    threat_level: str = "none"
    is_spam: bool = False
    is_phishing: bool = False
    is_manipulative: bool = False
    is_toxic: bool = False
    should_respond: bool = True
    response_priority: str = "medium"
    detected_language: str = "uz"
    confidence: float = 1.0
    reason: str = ""


class AnalysisAgent(BaseAgent):
    """Xabarni tahlil qiladi: sentiment, intent, xavf darajasi."""

    tier = ModelTier.FAST

    async def run(self, message: str) -> MessageAnalysis:
        return await self.analyze_message(message)

    async def analyze_message(self, message: str) -> MessageAnalysis:
        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": f"Xabar:\n{message}"}],
                system=ANALYSIS_PROMPT,
                temperature=0.1,
                max_tokens=512,
                response_schema=MESSAGE_ANALYSIS_SCHEMA,
            )
            data = parse_json_response(raw)
            return MessageAnalysis(**data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Tahlil parse xatosi: {e}. Default qaytarilmoqda.")
            return MessageAnalysis()
        except Exception as e:
            logger.error(f"Tahlil agenti xatosi: {e}")
            return MessageAnalysis()

    async def extract_facts(self, message: str) -> list[dict]:
        """Xabardan muhim faktlarni ajratib oladi."""
        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": f"Xabar:\n{message}"}],
                system=MEMORY_EXTRACTION_PROMPT,
                temperature=0.1,
                max_tokens=512,
                response_schema=EXTRACTED_FACTS_SCHEMA,
            )
            facts = parse_json_response(raw)
            # Sxema {"facts": [...]} shaklini majburlaydi; strukturali chiqish
            # o'chirilgan bo'lsa model yalang'och massiv qaytarishi mumkin.
            if isinstance(facts, dict):
                facts = facts.get("facts", [])
            if isinstance(facts, list):
                return facts
            return []
        except Exception as e:
            logger.warning(f"Fakt ajratishda xato: {e}")
            return []


analysis_agent = AnalysisAgent()

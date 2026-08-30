import json
from enum import Enum
from loguru import logger
from pydantic import BaseModel

from app.ai.agents.base_agent import BaseAgent
from app.ai.models import ModelTier
from app.ai.agents.json_parse import parse_json_response
from app.ai.prompts.system_prompt import CLASSIFIER_PROMPT
from app.ai.schemas import CLASSIFICATION_SCHEMA


class MessageCategory(str, Enum):
    IMPORTANT = "important"
    GREETING = "greeting"
    SIMPLE = "simple"
    GENERAL = "general"


class ClassificationResult(BaseModel):
    category: MessageCategory = MessageCategory.GENERAL
    language: str = "uz"
    confidence: float = 0.8
    reason: str = ""
    should_notify_owner: bool = False


class ClassifierAgent(BaseAgent):
    """Xabarni kategoriyalaydi va egaga bildirishnoma kerakligini aniqlaydi."""

    tier = ModelTier.FAST

    async def run(self, text: str, history: list[dict] | None = None) -> ClassificationResult:
        return await self.classify(text, history or [])

    async def classify(
        self, text: str, history: list[dict] | None = None
    ) -> ClassificationResult:
        context = ""
        if history:
            last = history[-3:]
            context = "\n".join(
                f"{m['role'].upper()}: {m['content'][:100]}" for m in last
            )

        user_content = f"Xabar:\n{text}"
        if context:
            user_content = f"Oldingi suhbat (qisqa):\n{context}\n\n{user_content}"

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": user_content}],
                system=CLASSIFIER_PROMPT,
                temperature=0.1,
                max_tokens=256,
                response_schema=CLASSIFICATION_SCHEMA,
            )
            data = parse_json_response(raw)
            result = ClassificationResult(**data)
            logger.debug(
                f"Klassifikatsiya: {result.category} "
                f"(lang={result.language}, conf={result.confidence:.2f}) — {result.reason}"
            )
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Klassifikatsiya parse xatosi: {e} — default: general")
            return ClassificationResult()
        except Exception as e:
            logger.error(f"Klassifikatsiya xatosi: {e}")
            return ClassificationResult()


classifier_agent = ClassifierAgent()

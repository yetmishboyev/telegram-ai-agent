"""FAQ agenti — bilim bazasidagi tasdiqlangan javobga tayanib, foydalanuvchi
savoliga tabiiy (va foydalanuvchi tilida) javob generatsiya qiladi."""
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.models import ModelTier
from app.ai.prompts.system_prompt import FAQ_ANSWER_PROMPT

# Bilim mos kelmaganda agent shu belgini qaytaradi → eskalatsiyaga tushadi.
NO_ANSWER = "NO_ANSWER"


class FaqAgent(BaseAgent):

    tier = ModelTier.BALANCED
    async def run(self, *args, **kwargs) -> str:
        return await self.generate(*args, **kwargs)

    async def generate(
        self,
        user_question: str,
        faq_question: str,
        faq_answer: str,
        lang: str = "uz",
    ) -> str | None:
        """Bilimga asoslanib javob qaytaradi; mos kelmasa None."""
        content = (
            f"Bilim (Shaxzodbek tasdiqlagan):\n"
            f"SAVOL: {faq_question}\n"
            f"JAVOB: {faq_answer}\n\n"
            f"Foydalanuvchi savoli ({lang} tilida):\n{user_question}"
        )
        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": content}],
                system=FAQ_ANSWER_PROMPT,
                temperature=0.4,
                max_tokens=400,
            )
        except Exception as e:
            logger.error(f"FAQ javob generatsiyada xato: {e}")
            return None

        reply = raw.strip()
        if not reply or reply.upper().strip(".!") == NO_ANSWER:
            logger.debug("FAQ agenti NO_ANSWER qaytardi — eskalatsiyaga tushadi")
            return None
        return reply


faq_agent = FaqAgent()

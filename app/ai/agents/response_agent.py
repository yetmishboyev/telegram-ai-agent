from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.prompts.system_prompt import (
    build_system_prompt,
    SUMMARY_PROMPT,
)
from app.database.models import TelegramUser


class ResponseAgent(BaseAgent):
    """Shaxzodbek uslubida javob generatsiya qiladi."""

    async def run(
        self,
        user: TelegramUser,
        current_message: str,
        history: list[dict],
        rag_context: str = "",
        relationship_type: str = "unknown",
        conversation_summary: str | None = None,
    ) -> str:
        return await self.generate_response(
            user=user,
            current_message=current_message,
            history=history,
            rag_context=rag_context,
            relationship_type=relationship_type,
            conversation_summary=conversation_summary,
        )

    async def generate_response(
        self,
        user: TelegramUser,
        current_message: str,
        history: list[dict],
        rag_context: str = "",
        relationship_type: str = "unknown",
        conversation_summary: str | None = None,
        schedule_context: str = "",
        style_examples: str = "",
    ) -> str:
        system_prompt = build_system_prompt(
            user=user,
            relationship_type=relationship_type,
            conversation_summary=conversation_summary,
            schedule_context=schedule_context,
            style_examples=style_examples,
        )

        if rag_context:
            system_prompt += f"\n\n{rag_context}"

        # Suhbat tarixini messages formatiga o'tkazish
        messages: list[dict] = []
        for msg in history:
            role = msg.get("role", "user")
            # LLM kutgan format: "user" | "assistant"
            if role == "agent":
                role = "assistant"
            messages.append({"role": role, "content": msg.get("content", "")})

        messages.append({"role": "user", "content": current_message})

        try:
            response = await self._call_llm(
                messages=messages,
                system=system_prompt,
                temperature=0.75,
                max_tokens=512,
            )
            response = response.strip()
            logger.debug(f"Javob generatsiya: user={user.telegram_id}, uzunlik={len(response)}")
            return response
        except Exception as e:
            logger.error(f"Javob generatsiyada xato: {e}")
            raise

    async def summarize(self, conversation_text: str) -> str:
        """Suhbat tarixini xulosalayd."""
        try:
            result = await self._call_llm(
                messages=[{"role": "user", "content": conversation_text}],
                system=SUMMARY_PROMPT,
                temperature=0.3,
                max_tokens=512,
            )
            return result.strip()
        except Exception as e:
            logger.error(f"Xulosa yaratishda xato: {e}")
            return "Suhbat xulosasi mavjud emas."


response_agent = ResponseAgent()

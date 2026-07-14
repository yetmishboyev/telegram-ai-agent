"""Eskalatsiya agenti — muhim murojaatga kontekstga sezgir, takrorlanmaydigan
yo'naltirish javobini generatsiya qiladi (statik shablon o'rniga)."""
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.prompts.system_prompt import ESCALATION_PROMPT


class EscalationAgent(BaseAgent):
    """Muhim/shaxsiy javob talab qiladigan xabarni egaga yo'naltirar ekan,
    foydalanuvchiga jonli, mavzuga bog'liq javob yozadi."""

    async def run(self, *args, **kwargs) -> str:
        return await self.generate(*args, **kwargs)

    async def generate(
        self,
        user_message: str,
        user_name: str,
        relationship_type: str,
        escalation_count: int,
        lang: str = "uz",
        status_context: str | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """Yo'naltirish javobini generatsiya qiladi.

        Args:
            escalation_count: shu foydalanuvchi bugun necha marta yo'naltirilgani (>=1).
            status_context: get_current_status() natijasi (ixtiyoriy).
            history: oxirgi suhbat (takrorlanmaslik uchun model o'z javoblarini ko'radi).
        """
        # Oldingi suhbat — model o'zining avvalgi yo'naltirish javoblarini ko'rib,
        # ularni takrorlamasligi uchun.
        messages: list[dict] = []
        for m in history or []:
            role = "assistant" if m.get("role") in ("assistant", "agent") else "user"
            messages.append({"role": role, "content": m.get("content", "")})

        status_line = (
            f"Shaxzodbekning hozirgi holati: {status_context}."
            if status_context
            else "Shaxzodbekning joriy holati haqida ma'lumot yo'q — umumiy javob ber."
        )
        instruction = (
            f"Foydalanuvchi ({user_name}, munosabat turi: {relationship_type}) "
            f"quyidagi xabarni yozdi:\n\"{user_message}\"\n\n"
            f"Til: {lang}\n"
            f"Bu foydalanuvchi bugun senga {escalation_count}-marta murojaat qilyapti.\n"
            f"{status_line}\n\n"
            "Yuqoridagi qoidalar asosida foydalanuvchiga yo'naltirish javobini yoz."
        )
        messages.append({"role": "user", "content": instruction})

        raw = await self._call_llm(
            messages=messages,
            system=ESCALATION_PROMPT,
            temperature=0.9,  # variatsiya uchun yuqori
            max_tokens=256,
        )
        reply = raw.strip()
        logger.debug(
            f"Eskalatsiya javobi generatsiya: name={user_name}, "
            f"count={escalation_count}, lang={lang}, uzunlik={len(reply)}"
        )
        return reply


escalation_agent = EscalationAgent()

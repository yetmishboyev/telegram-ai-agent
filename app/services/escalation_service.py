"""Interaktiv eskalatsiya — muhim murojaatga statik shablon o'rniga
kontekstga sezgir, kunlik hisobga bog'liq javob qaytaradi.

Har bir foydalanuvchi uchun "bugun necha marta yo'naltirildi" hisobi Redis'da
Toshkent yarim tunigacha saqlanadi, shunda agent takror yozganlarni tanib,
boshqacha (zerikarli bo'lmagan) javob bera oladi.
"""
from datetime import datetime, timedelta

import pytz
from loguru import logger

from app.ai.agents.escalation_agent import escalation_agent
from app.ai.memory.short_term import short_term_memory
from app.ai.prompts.system_prompt import IMPORTANT_RESPONSES
from app.database.models import TelegramUser
from app.database.redis import get_redis
from app.repositories.task_repo import get_current_status

_TZ = pytz.timezone("Asia/Tashkent")


def _seconds_until_tashkent_midnight() -> int:
    """Kunlik hisob yarim tunda (Toshkent) tiklanishi uchun TTL."""
    now = datetime.now(_TZ)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60, int((tomorrow - now).total_seconds()))


class EscalationService:
    def _count_key(self, telegram_id: int) -> str:
        return f"escalation_count:{telegram_id}"

    async def _incr_daily_count(self, telegram_id: int) -> int:
        """Kunlik yo'naltirish hisobini oshiradi va yangi qiymatni qaytaradi."""
        r = await get_redis()
        key = self._count_key(telegram_id)
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, _seconds_until_tashkent_midnight())
        return count

    def _static_fallback(self, lang: str, status: str | None) -> str:
        base = IMPORTANT_RESPONSES.get(lang, IMPORTANT_RESPONSES["uz"])
        if status:
            return f"{base}\n\n({status.capitalize()}.)"
        return base

    async def build_reply(
        self,
        user: TelegramUser,
        text: str,
        lang: str,
    ) -> str:
        """Foydalanuvchiga yuboriladigan yo'naltirish javobini tayyorlaydi.

        LLM generatsiyasi muvaffaqiyatsiz bo'lsa, statik shablonga qaytadi —
        eskalatsiya hech qachon javobsiz qolmasligi kerak.
        """
        count = await self._incr_daily_count(user.telegram_id)

        try:
            status = await get_current_status(lang)
        except Exception as e:
            logger.debug(f"Holat kontekstini olishda xato: {e}")
            status = None

        try:
            history = await short_term_memory.get_recent(user.telegram_id, n=6)
            return await escalation_agent.generate(
                user_message=text,
                user_name=user.display_name,
                relationship_type=user.relationship_type,
                escalation_count=count,
                lang=lang,
                status_context=status,
                history=history,
            )
        except Exception as e:
            logger.warning(f"Eskalatsiya generatsiyasida xato — statik shablonga qaytildi: {e}")
            return self._static_fallback(lang, status)


escalation_service = EscalationService()

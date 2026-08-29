"""Vision agenti — rasmni LLM (Claude/GPT-4o) orqali tasvirlab, matnga aylantiradi.

Natija odatiy (matnli) quvurga uzatiladi: rasm tavsifi xabar matni sifatida
klassifikatsiya, FAQ, eskalatsiya va javob generatsiyasidan o'tadi.
"""
import base64

from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.models import ModelTier
from app.config import settings

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_PROMPT = (
    "Bu rasmda nima borligini qisqa (1-2 gap) tasvirlab ber. "
    "Agar rasmda MATN bo'lsa, uni aynan o'qib yozib ber. "
    "Faqat tavsifni yoz, ortiqcha izohsiz."
)


class VisionAgent(BaseAgent):

    tier = ModelTier.BALANCED
    async def run(self, *args, **kwargs) -> str | None:
        return await self.describe(*args, **kwargs)

    async def describe(
        self, image_bytes: bytes, media_type: str = "image/jpeg"
    ) -> str | None:
        """Rasmni matnli tavsifga aylantiradi; xato bo'lsa None."""
        if media_type not in _ALLOWED_MIME:
            media_type = "image/jpeg"
        b64 = base64.standard_b64encode(image_bytes).decode()
        try:
            if settings.ai_provider == "anthropic":
                text = await self._describe_anthropic(b64, media_type)
            else:
                text = await self._describe_openai(b64, media_type)
            return (text or "").strip() or None
        except Exception as e:
            logger.error(f"Vision (rasm tavsifi) xatosi: {e}")
            return None

    async def _describe_anthropic(self, b64: str, media_type: str) -> str:
        # Rasm bloklari `_call_llm` imzosiga sig'maydi, shuning uchun chaqiruv
        # to'g'ridan-to'g'ri. Hisob yuritish esa qo'lda qo'shiladi — aks holda
        # vision xarajati o'lchovdan tushib qolardi.
        import time
        from app.ai import usage_log

        started = time.perf_counter()
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": b64,
                    }},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        usage_log.record(
            agent=self._agent_name, model=self._model, tier=self.tier.value,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens=usage_log.extract_usage(response),
        )
        return self._first_text(response)

    async def _describe_openai(self, b64: str, media_type: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{media_type};base64,{b64}",
                    }},
                ],
            }],
        )
        return response.choices[0].message.content or ""


vision_agent = VisionAgent()

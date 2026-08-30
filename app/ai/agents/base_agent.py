import time
from abc import ABC, abstractmethod
from typing import Any
from loguru import logger

from app.ai import usage_log
from app.ai.models import ModelTier, effort_for_temperature, sampling_mode
from app.config import settings


# Strukturali chiqish (`output_config.format`) butun jarayon uchun bitta marta
# o'chiriladi: agar API uni bir marta rad etsa, keyingi har chaqiruvda qayta
# urinish bekorga ikki so'rov yuborgan bo'lardi. Qayta yoqish uchun restart
# yetarli — bu ataylab, chunki sabab odatda SDK yoki model versiyasi.
_structured_output_ok = True


def _structured_output_enabled() -> bool:
    return _structured_output_ok


def _is_schema_rejection(error: Exception) -> bool:
    """Xato sxema tufaylimi — vaqtinchalik nosozlikdan ajratadi.

    Bu farq muhim: 429 (rate limit) yoki 529 (overloaded) ni "sxema rad
    etildi" deb tushunsak, API bo'g'ilayotgan paytda ikki barobar so'rov
    yuborardik va bitta tasodifiy nosozlik strukturali chiqishni butun
    jarayon uchun o'chirib qo'yardi. Faqat 400 (yaroqsiz so'rov) va
    `TypeError` (SDK parametrni umuman bilmaydi) sxema muammosi hisoblanadi.
    """
    if isinstance(error, TypeError):
        return True
    return getattr(error, "status_code", None) == 400


def _disable_structured_output() -> None:
    global _structured_output_ok
    if _structured_output_ok:
        _structured_output_ok = False
        logger.warning(
            "Strukturali chiqish o'chirildi — JSON javoblar matndan ajratib olinadi "
            "(parse_json_response). Qayta yoqish uchun ilovani restart qiling."
        )


def get_llm_client():
    """Provider sozlamasiga qarab LLM clientini qaytaradi."""
    if settings.ai_provider == "openai":
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=settings.openai_api_key)
    else:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=settings.anthropic_api_key)


class BaseAgent(ABC):
    """Barcha agentlar uchun asosiy sinf.

    Subklass `tier` ni belgilab o'ziga mos og'irlikdagi modelni tanlaydi:
    tasniflagich FAST, javob generatori BALANCED, kanal muallifi DEEP.
    Belgilamasa BALANCED qoladi.
    """

    tier: ModelTier = ModelTier.BALANCED

    def __init__(self) -> None:
        self._client = get_llm_client()
        self._model = settings.model_for_tier(self.tier.value)

    @property
    def _agent_name(self) -> str:
        return type(self).__name__

    async def _call_llm(
        self,
        messages: list[dict],
        system: str | list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        response_schema: dict | None = None,
    ) -> str:
        """LLM ga so'rov yuboradi va matn javob qaytaradi.

        `temperature` saqlanib qoldi — u chaqiruv joyidagi NIYATNI bildiradi
        (0.1 qat'iy, 0.9 ijodiy). Yangi avlod Anthropic modellari uni qabul
        qilmaydi, shuning uchun `_call_anthropic` ichida `effort` ga
        o'giriladi. OpenAI yo'lida esa o'zgarishsiz ishlatiladi.

        `system` matn yoki blok ro'yxati bo'lishi mumkin — ro'yxat shakli
        prompt keshlash uchun kerak (barqaror blok `cache_control` oladi).

        `response_schema` berilsa, javob shu JSON sxemasiga majburlanadi.
        """
        started = time.perf_counter()
        try:
            if settings.ai_provider == "anthropic":
                text, tokens = await self._call_anthropic(
                    messages, system, temperature, max_tokens, response_schema
                )
            else:
                text, tokens = await self._call_openai(
                    messages, system, temperature, max_tokens
                )
        except Exception as e:
            usage_log.record(
                agent=self._agent_name, model=self._model, tier=self.tier.value,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(e).__name__}: {e}",
            )
            logger.error(f"LLM xatosi ({settings.ai_provider}, {self._model}): {e}")
            raise

        usage_log.record(
            agent=self._agent_name, model=self._model, tier=self.tier.value,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens=tokens,
        )
        return text

    async def _call_anthropic(
        self,
        messages: list[dict],
        system: str | list[dict] | None,
        temperature: float,
        max_tokens: int,
        response_schema: dict | None = None,
    ) -> tuple[str, dict]:
        from anthropic import AsyncAnthropic
        client: AsyncAnthropic = self._client  # type: ignore

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        output_config: dict[str, Any] = {}

        # Effort va temperature bir-birini istisno qiladi. Qaysi biri
        # yuborilishini MODEL va SDK birgalikda hal qiladi — SDK 1.x da
        # `temperature` umuman yo'q (qarang: models.sampling_mode).
        mode = sampling_mode(self._model)
        if mode == "effort":
            output_config["effort"] = effort_for_temperature(temperature, self.tier.value)
        elif mode == "temperature":
            kwargs["temperature"] = temperature
        # mode == "none" → hech nima yuborilmaydi, model standart qiymatida ishlaydi

        structured = bool(response_schema) and _structured_output_enabled()
        if structured:
            output_config["format"] = {"type": "json_schema", "schema": response_schema}

        if output_config:
            kwargs["output_config"] = output_config

        try:
            response = await client.messages.create(**kwargs)
        except Exception as e:
            if not structured or not _is_schema_rejection(e):
                raise
            # Sxema qabul qilinmadi (model yoki API versiyasi qo'llab-quvvatlamaydi).
            # Bir marta sxemasiz qayta urinamiz: JSON'ni `parse_json_response`
            # baribir ajratib oladi, ya'ni xulq eski holatga qaytadi.
            logger.warning(
                f"Strukturali chiqish rad etildi ({self._model}): {e} — sxemasiz qayta urinilmoqda"
            )
            # Yangi nusxa quriladi, birinchi so'rov argumentlari o'zgarmaydi —
            # aks holda log yoki tekshiruv birinchi chaqiruvni buzilgan holda ko'rardi.
            retry_kwargs = dict(kwargs)
            retry_config = {k: v for k, v in output_config.items() if k != "format"}
            if retry_config:
                retry_kwargs["output_config"] = retry_config
            else:
                retry_kwargs.pop("output_config", None)
            response = await client.messages.create(**retry_kwargs)
            _disable_structured_output()

        return self._first_text(response), usage_log.extract_usage(response)

    @staticmethod
    def _first_text(response) -> str:
        """Javobdagi birinchi matn blokini oladi.

        `content[0]` ni ko'r-ko'rona olish xavfli: adaptive thinking yoqilgan
        modellarda birinchi blok `thinking` bo'lishi mumkin va unda `.text`
        umuman bo'lmaydi.

        Ikki qadam: avval `type == "text"` bo'lgan blok qidiriladi; topilmasa
        `.text` maydoni bor birinchi blok olinadi. Ikkinchi qadam SDK blok
        shakli o'zgarsa ham javobsiz qolmaslik uchun — fikrlash bloklari
        (`thinking`, `redacted_thinking`) da `.text` yo'q, shuning uchun bu
        zaxira yo'l noto'g'ri blokni tanlab qo'ymaydi.
        """
        blocks = getattr(response, "content", []) or []
        for block in blocks:
            if getattr(block, "type", None) == "text":
                return block.text
        for block in blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                return text
        return ""

    @staticmethod
    def _flatten_system(system: str | list[dict] | None) -> str | None:
        """Keshlash uchun bo'laklarga ajratilgan system promptni matnga yig'adi."""
        if system is None or isinstance(system, str):
            return system
        return "\n\n".join(
            block.get("text", "") for block in system if block.get("text")
        )

    async def _call_openai(
        self,
        messages: list[dict],
        system: str | list[dict] | None,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict]:
        from openai import AsyncOpenAI
        client: AsyncOpenAI = self._client  # type: ignore

        full_messages = []
        system_text = self._flatten_system(system)
        if system_text:
            full_messages.append({"role": "system", "content": system_text})
        full_messages.extend(messages)

        response = await client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = getattr(response, "usage", None)
        tokens = {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        } if usage else {}
        return response.choices[0].message.content or "", tokens

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        ...

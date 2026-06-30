from abc import ABC, abstractmethod
from typing import Any
from loguru import logger

from app.config import settings


def get_llm_client():
    """Provider sozlamasiga qarab LLM clientini qaytaradi."""
    if settings.ai_provider == "openai":
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=settings.openai_api_key)
    else:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=settings.anthropic_api_key)


class BaseAgent(ABC):
    """Barcha agentlar uchun asosiy sinf."""

    def __init__(self) -> None:
        self._client = get_llm_client()
        self._model = settings.active_model

    async def _call_llm(
        self,
        messages: list[dict],
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """LLM ga so'rov yuboradi va matn javob qaytaradi."""
        try:
            if settings.ai_provider == "anthropic":
                return await self._call_anthropic(messages, system, temperature, max_tokens)
            else:
                return await self._call_openai(messages, system, temperature, max_tokens)
        except Exception as e:
            logger.error(f"LLM xatosi ({settings.ai_provider}): {e}")
            raise

    async def _call_anthropic(
        self,
        messages: list[dict],
        system: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        from anthropic import AsyncAnthropic
        client: AsyncAnthropic = self._client  # type: ignore

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = await client.messages.create(**kwargs)
        return response.content[0].text

    async def _call_openai(
        self,
        messages: list[dict],
        system: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        from openai import AsyncOpenAI
        client: AsyncOpenAI = self._client  # type: ignore

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = await client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        ...

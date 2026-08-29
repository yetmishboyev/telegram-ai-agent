from functools import lru_cache
from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram ---
    telegram_api_id: int = Field(..., description="Telegram API ID from my.telegram.org")
    telegram_api_hash: str = Field(..., description="Telegram API Hash")
    telegram_phone: str = Field(..., description="+998901234567")
    telegram_session_name: str = "shaxzodbek_agent"
    telegram_bot_token: str = ""
    owner_telegram_id: int = 0
    reminder_hour: int = 7

    # --- AI Provider ---
    ai_provider: Literal["openai", "anthropic"] = "anthropic"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_model: str = "gpt-4o"
    # Anthropic modellari qatlam bo'yicha (app/ai/models.py — ModelTier).
    # `anthropic_model` asosiy (balanced) qatlam; qolgan ikkitasi berilmasa
    # o'shanga qaytadi, ya'ni bitta modelli sozlash ham ishlayveradi.
    anthropic_model: str = "claude-sonnet-5"
    # Bo'sh qoldirilsa asosiy modelga qaytadi — mavjud o'rnatmalar (`.env` da
    # faqat ANTHROPIC_MODEL bo'lgan) deploydan keyin jimgina boshqa modelga
    # o'tib ketmasin. Tavsiya etilgan qiymatlar `.env.example` da.
    anthropic_model_fast: str = ""
    anthropic_model_deep: str = ""

    # --- Ovoz transkripsiyasi ---
    # Claude audio qabul qilmaydi, shuning uchun bu alohida provayder.
    # AI_PROVIDER=anthropic bo'lsa ham OPENAI_API_KEY kerak; bo'lmasa ovozli
    # xabarlar eski yo'l bilan (yorliq bilan) qayta ishlanadi.
    voice_enabled: bool = True
    voice_provider: Literal["openai", "none"] = "openai"
    voice_model: str = "whisper-1"
    # Bo'sh = Whisper tilni o'zi aniqlaydi. "uz" majburlash o'zbekcha
    # aniqlikni oshirishi mumkin, lekin ruscha/inglizcha ovozni buzadi —
    # qaysi biri yaxshiroq ekani o'lchangandan keyin ma'lum bo'ladi
    # (scripts/check_transcription.py).
    voice_language: str = ""
    # Uzun ovozli xabar ham pul, ham kechikish — chegara qo'yiladi
    voice_max_duration_seconds: int = 300

    # --- Database ---
    database_url: str = Field(..., description="PostgreSQL asyncpg URL")
    database_echo: bool = False

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 86400

    # --- ChromaDB ---
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "agent_memory"

    # --- FastAPI ---
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    secret_key: str = Field(..., min_length=32)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    cors_origins: str = "https://178-156-189-1.sslip.io"

    # --- Admin ---
    admin_username: str = "admin"
    admin_password: str = Field(..., min_length=8)

    # --- Agent behaviour ---
    agent_enabled: bool = True
    agent_min_delay_seconds: float = 2.0
    agent_max_delay_seconds: float = 8.0
    agent_max_context_messages: int = 20
    agent_summary_threshold: int = 50
    confidence_threshold: float = 0.7

    # --- Environment ---
    environment: Literal["development", "production"] = "production"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_provider_keys(self) -> "Settings":
        key = self.anthropic_api_key if self.ai_provider == "anthropic" else self.openai_api_key
        if not key:
            raise ValueError(
                f"ai_provider='{self.ai_provider}' tanlangan, lekin tegishli API kalit "
                f"({'anthropic_api_key' if self.ai_provider == 'anthropic' else 'openai_api_key'}) bo'sh."
            )
        return self

    @property
    def active_model(self) -> str:
        return self.openai_model if self.ai_provider == "openai" else self.anthropic_model

    def model_for_tier(self, tier: str) -> str:
        """Qatlam nomini aniq model ID'siga aylantiradi.

        OpenAI yo'lida qatlam bo'linishi yo'q — bitta model qaytadi.
        Anthropic yo'lida bo'sh qoldirilgan qatlam asosiy modelga qaytadi.
        """
        if self.ai_provider == "openai":
            return self.openai_model
        if tier == "fast":
            return self.anthropic_model_fast or self.anthropic_model
        if tier == "deep":
            return self.anthropic_model_deep or self.anthropic_model
        return self.anthropic_model

    @property
    def active_api_key(self) -> str:
        return self.openai_api_key if self.ai_provider == "openai" else self.anthropic_api_key

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

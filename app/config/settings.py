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
    anthropic_model: str = "claude-sonnet-4-6"

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

    @property
    def active_api_key(self) -> str:
        return self.openai_api_key if self.ai_provider == "openai" else self.anthropic_api_key

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

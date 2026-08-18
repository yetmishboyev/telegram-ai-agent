from datetime import datetime, date as DateType
from enum import Enum as PyEnum
from typing import Optional
import uuid

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey,
    Integer, JSON, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MessageRole(str, PyEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class MessageType(str, PyEnum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    STICKER = "sticker"
    OTHER = "other"


class SentimentType(str, PyEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ThreatLevel(str, PyEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Profil ma'lumotlari (AI tomonidan to'ldiriladi)
    profession: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    interests: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    family_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    projects: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    important_dates: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Munosabat turi
    relationship_type: Mapped[str] = mapped_column(
        String(32), default="unknown"
    )  # friend, colleague, boss, stranger
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Statistika
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship("Message", back_populates="user")
    memory_entries: Mapped[list["MemoryEntry"]] = relationship(
        "MemoryEntry", back_populates="user"
    )

    @property
    def display_name(self) -> str:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.username or str(self.telegram_id)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=False), default=lambda: str(uuid.uuid4()), unique=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("telegram_users.id"), index=True
    )
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType), default=MessageType.TEXT
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # AI tahlil natijalari
    sentiment: Mapped[Optional[SentimentType]] = mapped_column(
        Enum(SentimentType), nullable=True
    )
    intent: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    importance_score: Mapped[float] = mapped_column(Float, default=0.5)
    threat_level: Mapped[ThreatLevel] = mapped_column(
        Enum(ThreatLevel), default=ThreatLevel.NONE
    )
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)

    # Agent javobi
    agent_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    was_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    was_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    admin_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Metadata
    media_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    reply_to_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="messages")


class MemoryEntry(Base):
    """Foydalanuvchi haqida uzoq muddatli xotira"""

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=False), default=lambda: str(uuid.uuid4()), unique=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("telegram_users.id"), index=True
    )

    category: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # personal, work, promise, event
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    vector_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["TelegramUser"] = relationship(
        "TelegramUser", back_populates="memory_entries"
    )


class ConversationSummary(Base):
    """Suhbat xulosasi — uzoq suhbatlar uchun"""

    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("telegram_users.id"), index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    from_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    to_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vector_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentConfig(Base):
    """Runtime konfiguratsiya — dashboard orqali o'zgartiriladi"""

    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminUser(Base):
    """Dashboard uchun admin foydalanuvchi"""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyTask(Base):
    """Kunlik reja — bot orqali boshqariladi"""

    __tablename__ = "daily_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[DateType] = mapped_column(Date, nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    start_time: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    end_time: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChannelPost(Base):
    """Kanalga yuborilgan postlar — analitika uchun"""

    __tablename__ = "channel_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    post_type: Mapped[str] = mapped_column(String(32), nullable=False)  # educational / news
    topic: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Yangilik postining mavzu kategoriyasi (mahsulot/tadqiqot/biznes/...).
    # Curation shu tarixga qarab ketma-ket bir xil temani tanlamaydi.
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    text_preview: Mapped[str] = mapped_column(String(512), nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    reactions: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    views_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FaqEntry(Base):
    """Bilim bazasi — ega o'rgatgan savol-javoblar (agent autonom javob berishi uchun)."""

    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    vector_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SubscriberSnapshot(Base):
    """Kanal obunachilari soni — davriy snapshot (o'sish dinamikasi uchun)."""

    __tablename__ = "subscriber_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AgentLog(Base):
    """Agent faoliyati loglari"""

    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

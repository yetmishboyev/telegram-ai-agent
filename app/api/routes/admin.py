from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database.session import get_db
from app.database.models import AdminUser, AgentConfig, TelegramUser
from app.api.dependencies import get_current_admin
from app.repositories.user_repo import user_repo
from app.ai.memory.short_term import short_term_memory
from app.ai.memory.manager import memory_manager

router = APIRouter(prefix="/admin", tags=["admin"])


class AgentToggle(BaseModel):
    enabled: bool


class BlacklistIn(BaseModel):
    telegram_id: int
    blacklisted: bool


class PauseIn(BaseModel):
    telegram_id: int
    paused: bool


class ConfigUpdate(BaseModel):
    key: str
    value: str
    description: str | None = None


class ClearMemoryIn(BaseModel):
    telegram_id: int


@router.post("/agent/toggle")
async def toggle_agent(
    body: AgentToggle,
    _: AdminUser = Depends(get_current_admin),
):
    await short_term_memory.set_agent_enabled(body.enabled)
    return {"enabled": body.enabled}


@router.get("/agent/status")
async def agent_status(_: AdminUser = Depends(get_current_admin)):
    enabled = await short_term_memory.is_agent_enabled()
    return {"enabled": enabled}


@router.post("/blacklist")
async def set_blacklist(
    body: BlacklistIn,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    await user_repo.set_blacklisted(db, body.telegram_id, body.blacklisted)
    await db.commit()
    return {"ok": True}


@router.post("/whitelist")
async def set_whitelist(
    body: BlacklistIn,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    await user_repo.set_whitelisted(db, body.telegram_id, body.blacklisted)
    await db.commit()
    return {"ok": True}


@router.post("/pause")
async def pause_user(
    body: PauseIn,
    _: AdminUser = Depends(get_current_admin),
):
    await short_term_memory.set_user_paused(body.telegram_id, body.paused)
    return {"paused": body.paused}


@router.post("/memory/clear")
async def clear_user_memory(
    body: ClearMemoryIn,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == body.telegram_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    await memory_manager.clear_user(db, user)
    await db.commit()
    return {"ok": True}


@router.get("/config")
async def get_configs(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(select(AgentConfig))
    configs = result.scalars().all()
    return [{"key": c.key, "value": c.value, "description": c.description} for c in configs]


@router.post("/config")
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    result = await db.execute(select(AgentConfig).where(AgentConfig.key == body.key))
    config = result.scalar_one_or_none()
    if config:
        config.value = body.value
        if body.description:
            config.description = body.description
    else:
        config = AgentConfig(key=body.key, value=body.value, description=body.description)
        db.add(config)
    await db.commit()
    return {"ok": True}

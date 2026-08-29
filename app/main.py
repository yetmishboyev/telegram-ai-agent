import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pathlib import Path

from app.config import settings
from app.database.session import AsyncSessionLocal
from app.database.models import AdminUser
from app.utils.security import hash_password
from app.middleware.logging import LoggingMiddleware
from app.api.routes import api_router
from app.api.routes.dashboard import router as dashboard_router
from sqlalchemy import select


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - {message}",
        colorize=True,
    )
    logger.add(
        "logs/agent_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )


async def purge_stale_style_samples() -> None:
    """Eski (sifat darvozasidan oldin saqlangan) uslub namunalarini tozalaydi.

    Fon vazifasi sifatida chaqiriladi — ishga tushishni kechiktirmasligi va
    ChromaDB javob bermasa ilovani yiqitmasligi kerak.
    """
    try:
        from app.ai.agents.style_learner import style_learner
        removed = await style_learner.purge_unlearnable()
        if removed:
            logger.info(f"Uslub namunalari tozalandi: {removed} ta o'chirildi")
    except Exception as e:
        logger.warning(f"Uslub namunalarini tozalashda xato: {e}")


async def create_admin_if_not_exists() -> None:
    """Faqat admin_users jadvali BUTUNLAY bo'sh bo'lsa, .env dagi boshlang'ich adminni yaratadi.

    Username bo'yicha qidirish emas — aks holda Sozlamalar sahifasidan
    username/parol o'zgartirilgandan keyin har restart'da .env dagi eski
    (potentsial sizib chiqqan) login qayta tirilib qolar edi.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AdminUser.id).limit(1))
        if result.scalar_one_or_none() is None:
            admin = AdminUser(
                username=settings.admin_username,
                hashed_password=hash_password(settings.admin_password),
            )
            db.add(admin)
            await db.commit()
            logger.info(f"Admin yaratildi: {settings.admin_username}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    Path("logs").mkdir(exist_ok=True)
    Path("sessions").mkdir(exist_ok=True)

    logger.info("=== Telegram AI Agent ishga tushmoqda ===")

    # DB jadvallari Alembic migratsiyalari orqali yaratiladi (Dockerfile CMD)
    await create_admin_if_not_exists()

    # Telegram UserBot ishga tushirish (3 urinish — session lock race condition uchun)
    from app.services.telegram_service import telegram_service
    telegram_task = None
    for attempt in range(1, 4):
        try:
            await telegram_service.start()
            telegram_task = asyncio.create_task(telegram_service.run_until_disconnected())
            logger.info("Telegram UserBot ulandi")
            break
        except Exception as e:
            logger.error(f"Telegram ulashda xato (urinish {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(5)

    # Telegram Bot (kunlik reja) ishga tushirish (3 urinish)
    from app.services.bot_service import bot_service
    bot_task = None
    for attempt in range(1, 4):
        try:
            await bot_service.start()
            bot_task = asyncio.create_task(bot_service.run_until_disconnected())
            break
        except Exception as e:
            logger.error(f"Bot serviceda xato (urinish {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(5)

    # Scheduler (ertalabki eslatma)
    from app.services.scheduler import scheduler_service
    try:
        scheduler_service.start(reminder_hour=settings.reminder_hour)
    except Exception as e:
        logger.error(f"Schedulerda xato: {e}")

    # Ovoz transkripsiyasi holatini bildiramiz — kalit yo'qligi jimgina
    # o'tib ketmasin, aks holda ovozli xabarlar sababsiz javobsiz qolgandek
    # ko'rinadi.
    from app.ai.transcriber import transcriber
    if transcriber.is_available():
        logger.info(f"Ovoz transkripsiyasi yoqilgan: {settings.voice_model} ({settings.voice_language})")
    else:
        logger.warning("Ovoz transkripsiyasi O'CHIQ — ovozli xabarlar matnga aylantirilmaydi")

    # Uslub namunalarini tozalash (fon vazifasi)
    purge_task = asyncio.create_task(purge_stale_style_samples())

    yield

    # Cleanup
    logger.info("Agent to'xtatilmoqda...")
    scheduler_service.stop()
    for task in [telegram_task, bot_task, purge_task]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await telegram_service.disconnect()
    await bot_service.disconnect()
    logger.info("Agent to'xtatildi.")


app = FastAPI(
    title="Telegram AI Agent",
    description="Shaxzodbek uchun shaxsiy Telegram AI Agent",
    version="1.0.0",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url="/api/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(LoggingMiddleware)

# Routerlar
app.include_router(api_router)
app.include_router(dashboard_router)

# Static fayllar
static_dir = Path(__file__).parent.parent / "dashboard" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
async def health():
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as sa_text
    from app.database.session import engine
    from app.database.redis import get_redis
    from app.ai.vector_db.chroma_client import chroma_client
    from app.services.telegram_service import telegram_service
    from app.services.bot_service import bot_service

    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        logger.error(f"Health check: database xatosi: {e}")
        checks["database"] = "error"

    try:
        r = await get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.error(f"Health check: redis xatosi: {e}")
        checks["redis"] = "error"

    try:
        await chroma_client.count()
        checks["chromadb"] = "ok"
    except Exception as e:
        logger.error(f"Health check: chromadb xatosi: {e}")
        checks["chromadb"] = "error"

    checks["telegram_userbot"] = (
        "connected" if telegram_service.is_ready() else "disconnected"
    )
    checks["telegram_bot"] = (
        "connected"
        if bot_service._client and bot_service._client.is_connected()
        else "disconnected"
    )

    critical_ok = checks["database"] == "ok" and checks["redis"] == "ok"

    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content={
            "status": "ok" if critical_ok else "degraded",
            "agent": "Telegram AI Agent v1.0",
            "checks": checks,
        },
    )

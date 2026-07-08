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


async def create_admin_if_not_exists() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AdminUser).where(AdminUser.username == settings.admin_username)
        )
        admin = result.scalar_one_or_none()
        if not admin:
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

    yield

    # Cleanup
    logger.info("Agent to'xtatilmoqda...")
    scheduler_service.stop()
    for task in [telegram_task, bot_task]:
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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {"status": "ok", "agent": "Telegram AI Agent v1.0"}

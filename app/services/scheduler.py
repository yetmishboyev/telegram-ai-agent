import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._tz = pytz.timezone("Asia/Tashkent")

    def start(self, reminder_hour: int = 7) -> None:
        from app.services.bot_service import bot_service
        from app.services.channel_poster import channel_poster

        # Ertalabki eslatma — har kuni
        self._scheduler.add_job(
            func=bot_service.send_morning_reminder,
            trigger=CronTrigger(hour=reminder_hour, minute=0, timezone=self._tz),
            id="morning_reminder",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Du-Ju (0-4): 09:00 — AI ta'limiy post
        self._scheduler.add_job(
            func=channel_poster.post_educational,
            trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=self._tz),
            id="channel_educational",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Du-Ju (0-4): 12:00 — AI yangiliklari post
        self._scheduler.add_job(
            func=channel_poster.post_news,
            trigger=CronTrigger(day_of_week="mon-fri", hour=12, minute=0, timezone=self._tz),
            id="channel_news_noon",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Du-Ju (0-4): 16:00 — AI yangiliklari post
        self._scheduler.add_job(
            func=channel_poster.post_news,
            trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=self._tz),
            id="channel_news_evening",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Yakshanba (6): 12:00 — Haftalik dayjest (faqat bitta post)
        self._scheduler.add_job(
            func=channel_poster.post_weekly_digest,
            trigger=CronTrigger(day_of_week="sun", hour=12, minute=0, timezone=self._tz),
            id="channel_weekly_digest",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Har 2 soatda — kanal postlari ko'rishlarini yangilash
        self._scheduler.add_job(
            func=channel_poster.refresh_views,
            trigger=CronTrigger(hour="*/2", minute=30, timezone=self._tz),
            id="channel_views_refresh",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Har 6 soatda — obunachilar soni snapshot (o'sish dinamikasi)
        self._scheduler.add_job(
            func=channel_poster.snapshot_subscribers,
            trigger=CronTrigger(hour="*/6", minute=15, timezone=self._tz),
            id="subscriber_snapshot",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self._scheduler.start()
        logger.info(f"Scheduler ishga tushdi: har kuni {reminder_hour:02d}:00 (Toshkent vaqti)")
        logger.info("Kanal postlar: Du-Ju 09:00 ta'lim · 12:00 yangilik · 16:00 yangilik | Sha dam olish | Yak 12:00 dayjest")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler to'xtatildi")


scheduler_service = SchedulerService()

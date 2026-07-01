from datetime import date
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DailyTask

TASK_EMOJIS = {"meeting": "🤝", "conference": "🎤", "class": "📚", "other": "✅"}
TASK_NAMES  = {"meeting": "Yig'ilish", "conference": "Konferensiya", "class": "Dars", "other": "Boshqa"}


async def get_today_tasks(db: AsyncSession) -> list[DailyTask]:
    result = await db.execute(
        select(DailyTask)
        .where(DailyTask.date == date.today())
        .order_by(DailyTask.start_time.nulls_last(), DailyTask.created_at)
    )
    return list(result.scalars().all())


async def get_tasks_by_date(db: AsyncSession, day: date) -> list[DailyTask]:
    result = await db.execute(
        select(DailyTask)
        .where(DailyTask.date == day)
        .order_by(DailyTask.start_time.nulls_last(), DailyTask.created_at)
    )
    return list(result.scalars().all())


async def create_task(
    db: AsyncSession,
    task_type: str,
    start_time: str | None = None,
    end_time: str | None = None,
    title: str | None = None,
    task_date: date | None = None,
) -> DailyTask:
    task = DailyTask(
        date=task_date or date.today(),
        task_type=task_type,
        title=title,
        start_time=start_time,
        end_time=end_time,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def mark_done(db: AsyncSession, task_id: int) -> DailyTask | None:
    result = await db.execute(select(DailyTask).where(DailyTask.id == task_id))
    task = result.scalar_one_or_none()
    if task:
        task.is_done = True
        await db.commit()
    return task


async def delete_task(db: AsyncSession, task_id: int) -> bool:
    result = await db.execute(select(DailyTask).where(DailyTask.id == task_id))
    task = result.scalar_one_or_none()
    if task:
        await db.delete(task)
        await db.commit()
        return True
    return False


async def get_current_status(lang: str = "uz") -> str | None:
    """Hozirgi vaqtda faol vazifani til bo'yicha qisqa matn sifatida qaytaradi."""
    import pytz
    from datetime import datetime
    from app.database.session import AsyncSessionLocal

    tz = pytz.timezone("Asia/Tashkent")
    now_str = datetime.now(tz).strftime("%H:%M")

    async with AsyncSessionLocal() as db:
        tasks = await get_today_tasks(db)

    if not tasks:
        return None

    for t in tasks:
        if t.is_done or not t.start_time:
            continue
        emoji = TASK_EMOJIS.get(t.task_type, "•")
        label = t.title if t.title else TASK_NAMES.get(t.task_type, t.task_type)

        if t.end_time:
            # Start va end vaqti bor — hozir oralig'ida ekanligini tekshir
            if t.start_time <= now_str <= t.end_time:
                if lang == "ru":
                    return f"сейчас Шахзодбек занят: {emoji} {label} (до {t.end_time})"
                elif lang == "en":
                    return f"Shaxzodbek is currently busy: {emoji} {label} (until {t.end_time})"
                else:
                    return f"hozirda Shaxzodbek band: {emoji} {label} ({t.end_time} gacha)"
        else:
            # Faqat start vaqti bor — boshlagan bo'lsa, hozir ham davom etyapti deb hisobla
            if t.start_time <= now_str:
                if lang == "ru":
                    return f"сейчас Шахзодбек занят: {emoji} {label}"
                elif lang == "en":
                    return f"Shaxzodbek is currently busy: {emoji} {label}"
                else:
                    return f"hozirda Shaxzodbek band: {emoji} {label}"

    # Faol vazifa yo'q — keyingi rejalashtirilganini ko'rsat
    for t in tasks:
        if t.is_done or not t.start_time:
            continue
        if t.start_time > now_str:
            label = t.title if t.title else TASK_NAMES.get(t.task_type, t.task_type)
            if lang == "ru":
                return f"в {t.start_time} у Шахзодбека запланировано: {label}"
            elif lang == "en":
                return f"Shaxzodbek has {label} scheduled at {t.start_time}"
            else:
                return f"bugun soat {t.start_time} da rejalashtirilgan: {label}"

    return None


async def get_schedule_context() -> str:
    """AI agent uchun bugungi jadval konteksti."""
    import pytz
    from datetime import datetime
    from app.database.session import AsyncSessionLocal

    tz = pytz.timezone("Asia/Tashkent")
    now_str = datetime.now(tz).strftime("%H:%M")

    async with AsyncSessionLocal() as db:
        tasks = await get_today_tasks(db)
    lines = ["\n## Shaxzodbekning bugungi jadvali:"]

    current_task = None
    next_task = None

    for t in tasks:
        if t.is_done:
            continue
        emoji = TASK_EMOJIS.get(t.task_type, "•")
        time_str = t.start_time or ""
        if t.end_time:
            time_str += f"–{t.end_time}"
        title = f" ({t.title})" if t.title else ""
        name = TASK_NAMES.get(t.task_type, t.task_type)
        lines.append(f"- {time_str}: {emoji} {name}{title}")

        if t.start_time and t.end_time:
            if t.start_time <= now_str <= t.end_time:
                current_task = t
        elif t.start_time and not current_task:
            if t.start_time > now_str and not next_task:
                next_task = t

    if current_task:
        emoji = TASK_EMOJIS.get(current_task.task_type, "•")
        label = current_task.title if current_task.title else TASK_NAMES.get(current_task.task_type, "")
        end_info = f", {current_task.end_time} da tugaydi" if current_task.end_time else ""
        lines.append(
            f"\n⚡ HOZIRGI HOLAT: Shaxzodbek hozir {emoji} {label}da{end_info}. "
            f"Suhbatdosh muloqot so'rasa bunga mos ravishda javob ber."
        )
    elif next_task:
        label = next_task.title if next_task.title else TASK_NAMES.get(next_task.task_type, "")
        lines.append(
            f"\n📌 Keyingi vazifa: {next_task.start_time} da {label}."
        )

    return "\n".join(lines)

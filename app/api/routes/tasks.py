from datetime import date as DateType
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.database.models import AdminUser, DailyTask
from app.api.dependencies import get_current_admin
from app.repositories.task_repo import (
    get_today_tasks, get_tasks_by_date, create_task, mark_done, delete_task,
    TASK_EMOJIS, TASK_NAMES,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def task_to_dict(t: DailyTask) -> dict:
    return {
        "id": t.id,
        "date": t.date.isoformat(),
        "task_type": t.task_type,
        "emoji": TASK_EMOJIS.get(t.task_type, "✅"),
        "type_name": TASK_NAMES.get(t.task_type, t.task_type),
        "title": t.title,
        "start_time": t.start_time,
        "end_time": t.end_time,
        "is_done": t.is_done,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/today")
async def get_today(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    tasks = await get_today_tasks(db)
    return [task_to_dict(t) for t in tasks]


@router.get("/date/{day}")
async def get_by_date(
    day: DateType,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    tasks = await get_tasks_by_date(db, day)
    return [task_to_dict(t) for t in tasks]


class TaskCreate(BaseModel):
    task_type: str = "other"
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    task_date: DateType | None = None


@router.post("/")
async def add_task(
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    task = await create_task(
        db,
        task_type=body.task_type,
        start_time=body.start_time,
        end_time=body.end_time,
        title=body.title,
        task_date=body.task_date,
    )
    return task_to_dict(task)


@router.patch("/{task_id}/done")
async def done_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    task = await mark_done(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Topilmadi")
    return task_to_dict(task)


@router.delete("/{task_id}")
async def remove_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    ok = await delete_task(db, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Topilmadi")
    return {"ok": True}

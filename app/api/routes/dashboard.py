from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "dashboard" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/login", response_class=HTMLResponse)
async def dashboard_login(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/chats", response_class=HTMLResponse)
async def dashboard_chats(request: Request):
    return templates.TemplateResponse(request, "chats.html")


@router.get("/chats/{telegram_id}", response_class=HTMLResponse)
async def dashboard_chat_detail(request: Request, telegram_id: int):
    return templates.TemplateResponse(request, "chat_detail.html", {"telegram_id": telegram_id})


@router.get("/memory", response_class=HTMLResponse)
async def dashboard_memory(request: Request):
    return templates.TemplateResponse(request, "memory.html")


@router.get("/logs", response_class=HTMLResponse)
async def dashboard_logs(request: Request):
    return templates.TemplateResponse(request, "logs.html")


@router.get("/settings", response_class=HTMLResponse)
async def dashboard_settings(request: Request):
    return templates.TemplateResponse(request, "settings.html")

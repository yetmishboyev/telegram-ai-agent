from fastapi import APIRouter
from app.api.routes.auth import router as auth_router
from app.api.routes.chats import router as chats_router
from app.api.routes.admin import router as admin_router
from app.api.routes.stats import router as stats_router
from app.api.routes.memory import router as memory_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.channel import router as channel_router
from app.api.routes.costs import router as costs_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(chats_router)
api_router.include_router(admin_router)
api_router.include_router(stats_router)
api_router.include_router(memory_router)
api_router.include_router(tasks_router)
api_router.include_router(channel_router)
api_router.include_router(costs_router)

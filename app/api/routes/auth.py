from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database.session import get_db
from app.database.models import AdminUser
from app.utils.security import verify_password, create_access_token
from app.database.redis import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 900  # 15 daqiqa


class Token(BaseModel):
    access_token: str
    token_type: str


def _login_fail_key(ip: str) -> str:
    return f"login_fail:{ip}"


async def _enforce_rate_limit(ip: str) -> None:
    r = await get_redis()
    attempts = await r.get(_login_fail_key(ip))
    if attempts and int(attempts) >= MAX_LOGIN_ATTEMPTS:
        ttl = await r.ttl(_login_fail_key(ip))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Juda ko'p noto'g'ri urinish. {max(ttl, 1)} soniyadan keyin qayta urinib ko'ring.",
        )


async def _register_failed_attempt(ip: str) -> None:
    r = await get_redis()
    key = _login_fail_key(ip)
    attempts = await r.incr(key)
    if attempts == 1:
        await r.expire(key, LOGIN_LOCKOUT_SECONDS)


async def _reset_attempts(ip: str) -> None:
    r = await get_redis()
    await r.delete(_login_fail_key(ip))


@router.post("/token", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Token:
    ip = request.client.host if request.client else "unknown"
    await _enforce_rate_limit(ip)

    result = await db.execute(
        select(AdminUser).where(
            AdminUser.username == form_data.username,
            AdminUser.is_active == True,
        )
    )
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(form_data.password, admin.hashed_password):
        await _register_failed_attempt(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username yoki parol noto'g'ri",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await _reset_attempts(ip)
    token = create_access_token(data={"sub": admin.username})
    return Token(access_token=token, token_type="bearer")

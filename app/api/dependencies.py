from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.database.models import AdminUser
from app.utils.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autentifikatsiya muvaffaqiyatsiz",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if payload is None:
        raise credentials_exception

    username = payload.get("sub")
    if username is None:
        raise credentials_exception

    result = await db.execute(
        select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise credentials_exception

    return admin

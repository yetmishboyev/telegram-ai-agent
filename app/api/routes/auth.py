from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database.session import get_db
from app.database.models import AdminUser
from app.utils.security import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class Token(BaseModel):
    access_token: str
    token_type: str


@router.post("/token", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Token:
    result = await db.execute(
        select(AdminUser).where(
            AdminUser.username == form_data.username,
            AdminUser.is_active == True,
        )
    )
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(form_data.password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username yoki parol noto'g'ri",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(data={"sub": admin.username})
    return Token(access_token=token, token_type="bearer")

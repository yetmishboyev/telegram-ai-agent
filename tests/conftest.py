import pytest

from app.database.session import AsyncSessionLocal


@pytest.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

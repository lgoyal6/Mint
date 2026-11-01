import importlib.util

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from mint.backend.settings import Settings


class Base(DeclarativeBase):
    pass


settings = Settings()


def make_engine(database_url: str | None = None) -> AsyncEngine:
    url = database_url or settings.database_url
    if not url:
        raise RuntimeError("DATABASE_URL is required to create the async database engine")
    return create_async_engine(url, pool_size=10, max_overflow=20)


engine: AsyncEngine | None = (
    create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
    )
    if settings.database_url and importlib.util.find_spec("asyncpg") is not None
    else None
)

SessionLocal: async_sessionmaker[AsyncSession] | None = (
    async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) if engine is not None else None
)

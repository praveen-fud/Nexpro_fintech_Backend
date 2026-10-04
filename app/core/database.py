from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()


def _resolve_database_url(raw_url: str) -> str:
    """
    Hosting providers (Railway, Heroku, Render, ...) hand out bare
    postgres:// / postgresql:// connection strings with no driver suffix.
    create_async_engine needs an async-capable one, so normalize to the
    asyncpg driver this project actually installs rather than relying on
    every deploy's env var being typed with +asyncpg by hand.
    """
    url = make_url(raw_url)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)


database_url = _resolve_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine = create_async_engine(database_url, echo=False, connect_args=connect_args)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Nexpro Fintech API"
    environment: str = "development"

    # Defaults to a local SQLite file so the app runs with zero external
    # services. Point DATABASE_URL at Postgres (e.g.
    # postgresql+asyncpg://user:pass@localhost:5432/nexpro) for a
    # production-like setup via Docker Compose.
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    jwt_secret: str = "insecure-dev-secret-change-me-before-production-use"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    refresh_cookie_name: str = "nexpro_refresh_token"

    cors_origins: list[str] = ["http://localhost:5173"]

    upload_dir: str = "./uploads"
    max_upload_mb: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()

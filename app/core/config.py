from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Nexpro Paytech API"
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

    # Company receiving accounts shown to customers on the Add Money pages.
    # Real values MUST be set in production; development falls back to demo
    # values (flagged `isDemo` to the UI) so the flow can be tried locally.
    payee_upi_id: str = ""
    payee_upi_name: str = ""
    payee_bank_account_name: str = ""
    payee_bank_name: str = ""
    payee_bank_account_number: str = ""
    payee_bank_ifsc: str = ""

    # Razorpay (card payments). Leave blank to disable cards. Use rzp_test_*
    # keys outside production. The secret and webhook secret never leave the server.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    upload_dir: str = "./uploads"
    max_upload_mb: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()

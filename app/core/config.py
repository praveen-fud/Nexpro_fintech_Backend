from __future__ import annotations

import json
import logging
import secrets
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

# The value that used to be this app's committed default JWT secret. It is
# public (it sits in this repo's git history), so anything signed with it is
# forgeable by anyone who has read the source. Production boot refuses it.
_KNOWN_LEAKED_SECRETS = {
    "insecure-dev-secret-change-me-before-production-use",
    "change-me",
    "changeme",
    "secret",
}

_MIN_SECRET_LENGTH = 32

# The local-dev secret lives in a gitignored file next to the app, so a dev
# restart does not invalidate every open session, while no real secret ever
# enters the repo. Production must supply JWT_SECRET via the environment.
_DEV_SECRET_FILE = Path(__file__).resolve().parents[2] / ".dev-jwt-secret"


class ConfigurationError(RuntimeError):
    """Raised at startup when the environment is unsafe to serve traffic."""


def _load_or_create_dev_secret() -> str:
    try:
        if _DEV_SECRET_FILE.exists():
            existing = _DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(existing) >= _MIN_SECRET_LENGTH:
                return existing
        generated = secrets.token_urlsafe(48)
        _DEV_SECRET_FILE.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        # Read-only filesystem (some CI sandboxes): fall back to a per-process
        # secret. Tokens then die with the process, which is fine for dev.
        return secrets.token_urlsafe(48)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Nexpro Paytech API"
    environment: str = "development"

    # Defaults to a local SQLite file so the app runs with zero external
    # services. Point DATABASE_URL at Postgres (e.g.
    # postgresql+asyncpg://user:pass@localhost:5432/nexpro) for a
    # production-like setup via Docker Compose.
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    # No usable default on purpose. A committed fallback secret means a deploy
    # that forgets the env var silently accepts tokens anyone can forge.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15

    # Session policy (money app): a session ends after this much user
    # inactivity, and can never outlive the absolute cap even if the user is
    # continuously active. The browser enforces the idle limit to the second;
    # the server enforces it too (plus a small grace for heartbeat latency)
    # so an abandoned or tampered client cannot keep a session alive.
    idle_timeout_minutes: int = 15
    idle_grace_seconds: int = 30
    session_max_hours: int = 12
    refresh_cookie_name: str = "nexpro_refresh_token"

    # When the SPA is served from a different site than the API (static host
    # -> Railway API), browsers only send the refresh cookie on cross-site
    # XHR if SameSite=None, which in turn requires Secure. Keep "lax" for
    # same-origin deployments and local dev.
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # NoDecode: without it pydantic-settings json.loads() the raw env value
    # before any validator runs, so the comma-separated form below would
    # raise a SettingsError at boot instead of being parsed by _split_csv.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Host header allowlist (blocks Host-header poisoning / DNS rebinding).
    # ["*"] disables the check: fine in dev, rejected in production.
    trusted_hosts: Annotated[list[str], NoDecode] = ["*"]

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
    # Hard cap on any request body, enforced before the body is buffered so a
    # huge upload cannot be read into memory first.
    max_request_body_mb: int = 10

    # Interactive API docs describe every endpoint and schema. Useful in dev,
    # free reconnaissance in production. Unset means "on outside production",
    # so a new deploy is never surprised by a public schema dump; set it to
    # true explicitly if you really do want published docs in production.
    enable_docs: bool | None = None

    # Dormant endpoint left from the pre-OTP-removal design. It mutates
    # account state with no authentication, so it stays off unless
    # deliberately switched on alongside a real OTP provider.
    enable_otp_endpoint: bool = False

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    # "<requests>/<seconds>" windows, keyed on client IP unless noted.
    rate_limit_default: str = "300/60"
    rate_limit_login: str = "10/300"
    rate_limit_signup: str = "5/3600"
    rate_limit_refresh: str = "60/300"
    rate_limit_write: str = "30/60"
    rate_limit_upload: str = "10/600"
    # Per-account failed-login lockout, independent of the per-IP limit, so a
    # distributed attack spread across many IPs still trips it.
    login_max_failures: int = 8
    login_lockout_seconds: int = 900
    # How many proxy hops sit in front of the app. Railway and most PaaS = 1.
    # Never trust the whole X-Forwarded-For chain: a client can forge the
    # left-hand entries and rotate its way past any per-IP limit.
    trusted_proxy_hops: int = 1

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept both JSON (``["https://a"]``) and comma-separated
        (``https://a,https://b``) forms. Deploy dashboards make the second
        far easier to type without a syntax mistake.

        These fields are annotated NoDecode, so this is the ONLY place the
        raw environment string gets parsed — both shapes have to be handled
        here, including the JSON one that pydantic-settings would otherwise
        have decoded for us.
        """
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Expected a JSON list or a comma-separated list, got {raw!r}."
                    ) from exc
                return decoded
            return [part.strip() for part in raw.split(",") if part.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def cookie_secure(self) -> bool:
        # SameSite=None is only honoured on Secure cookies, so it forces this
        # on regardless of environment.
        return self.is_production or self.cookie_samesite == "none"

    @property
    def docs_enabled(self) -> bool:
        """Secure-by-default: docs are served unless we are in production and
        nobody explicitly opted in."""
        if self.enable_docs is None:
            return not self.is_production
        return self.enable_docs

    @model_validator(mode="after")
    def _apply_environment_rules(self) -> Settings:
        if not self.jwt_secret:
            if self.is_production:
                raise ConfigurationError(
                    "JWT_SECRET is not set. Generate one with "
                    '`python -c "import secrets; print(secrets.token_urlsafe(48))"` '
                    "and set it as an environment variable on the service."
                )
            object.__setattr__(self, "jwt_secret", _load_or_create_dev_secret())

        if self.jwt_secret.strip().lower() in _KNOWN_LEAKED_SECRETS:
            raise ConfigurationError(
                "JWT_SECRET is set to a publicly-known placeholder value. Anyone "
                "who has seen this repo could forge a SUPER_ADMIN token with it. "
                "Set a unique random secret."
            )

        if len(self.jwt_secret) < _MIN_SECRET_LENGTH:
            message = (
                f"JWT_SECRET is only {len(self.jwt_secret)} characters; "
                f"at least {_MIN_SECRET_LENGTH} are required for {self.jwt_algorithm}."
            )
            if self.is_production:
                raise ConfigurationError(message)
            warnings.warn(message, stacklevel=2)

        if self.jwt_algorithm not in {"HS256", "HS384", "HS512"}:
            raise ConfigurationError(
                f"Unsupported JWT_ALGORITHM {self.jwt_algorithm!r}. This build "
                "signs with a shared secret, so only HMAC algorithms are valid. "
                "In particular 'none' must never be accepted."
            )

        if self.cookie_samesite == "none" and not self.is_production:
            warnings.warn(
                "COOKIE_SAMESITE=none sets Secure on the refresh cookie, which "
                "browsers drop over plain http — local dev login will not stick.",
                stacklevel=2,
            )

        if self.is_production:
            if self.database_url.startswith("sqlite"):
                raise ConfigurationError(
                    "DATABASE_URL still points at SQLite in production. A file "
                    "database on an ephemeral container filesystem loses every "
                    "account and ledger entry on redeploy. Point it at Postgres."
                )
            if "*" in self.cors_origins:
                raise ConfigurationError(
                    "CORS_ORIGINS contains '*'. Combined with credentialed "
                    "requests that lets any website call this API as a signed-in "
                    "user. List the frontend's exact origin(s) instead."
                )
            insecure_origins = [
                origin
                for origin in self.cors_origins
                if origin.startswith("http://") and not origin.startswith("http://localhost")
            ]
            if insecure_origins:
                raise ConfigurationError(
                    "CORS_ORIGINS contains plaintext http origins in production: "
                    f"{insecure_origins}. Use https so access tokens are not sent "
                    "in the clear."
                )
            if "*" in self.trusted_hosts:
                raise ConfigurationError(
                    "TRUSTED_HOSTS is '*' in production. Set it to this service's "
                    "own hostname(s) so a forged Host header cannot be reflected "
                    "into links, cache keys or password-reset URLs."
                )
            if self.enable_docs is True:
                # Not fatal (some teams publish docs deliberately) but it
                # should be a conscious choice, so say so loudly.
                logger.warning(
                    "ENABLE_DOCS is true in production: /docs, /redoc and "
                    "/openapi.json are publicly readable."
                )
            if self.enable_otp_endpoint:
                logger.warning(
                    "ENABLE_OTP_ENDPOINT is true: /auth/verify-otp accepts any "
                    "6-digit code and requires no authentication."
                )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

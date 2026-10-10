"""Idle / absolute session expiry rules, shared by the auth router and the
current-user dependency so there is exactly one definition of "live"."""

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.models.user import AuthSession

settings = get_settings()


def _aware(dt: datetime) -> datetime:
    # SQLite returns tz-naive datetimes even for DateTime(timezone=True).
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def session_is_live(session: AuthSession | None, now: datetime | None = None) -> bool:
    if session is None or session.revoked_at is not None:
        return False
    now = now or datetime.now(UTC)
    idle_limit = timedelta(minutes=settings.idle_timeout_minutes, seconds=settings.idle_grace_seconds)
    if now - _aware(session.last_active_at) > idle_limit:
        return False
    return now - _aware(session.started_at) <= timedelta(hours=settings.session_max_hours)

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

settings = get_settings()


def create_access_token(user_id: uuid.UUID, role: str, session_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "sid": str(session_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload


def generate_refresh_token() -> str:
    """High-entropy opaque token — not a JWT. We look it up by hash in the DB,
    which lets us revoke/rotate it, unlike a stateless JWT refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    # SHA-256 is appropriate here (unlike for passwords) because the input
    # already has ~288 bits of entropy — there's nothing to brute-force.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry(session_started_at: datetime) -> datetime:
    # A refresh token can never outlive the session's absolute cap.
    return session_started_at + timedelta(hours=settings.session_max_hours)

import uuid

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role
from app.models.user import AuthSession, User
from app.security.sessions import session_is_live
from app.security.tokens import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please sign in to continue.")
SESSION_EXPIRED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session expired due to inactivity. Please sign in again."
)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise UNAUTHORIZED
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise UNAUTHORIZED from exc

    # Idle/absolute expiry. Deliberately read-only: ordinary API calls (incl.
    # background polling) must NOT extend the session - only the client's
    # explicit activity heartbeat does (see /auth/heartbeat).
    auth_session = (await db.execute(select(AuthSession).where(AuthSession.id == session_id))).scalar_one_or_none()
    if auth_session is None or auth_session.user_id != user_id or not session_is_live(auth_session):
        raise SESSION_EXPIRED

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UNAUTHORIZED
    request.state.auth_session = auth_session
    return user


def require_roles(*roles: Role):
    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user

    return _check

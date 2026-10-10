from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi.errors import RateLimitExceeded  # noqa: F401 — imported so the handler is registered
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.models.enums import Role
from app.models.user import AuthSession, RefreshToken, User
from app.schemas.auth import LoginRequest, LoginResponse, RefreshResponse, SignUpRequest, VerifyOtpRequest
from app.schemas.user import UserResponse
from app.security.deps import get_current_user
from app.security.passwords import hash_password, verify_password
from app.security.sessions import session_is_live
from app.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    # SameSite=None + Secure is required for cross-origin cookie delivery
    # (frontend and backend on different Railway domains).  In development
    # SameSite=Lax + no Secure is fine because the Vite proxy makes it
    # same-origin.
    is_prod = settings.environment == "production"
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        httponly=True,
        secure=is_prod,
        samesite="none" if is_prod else "lax",
        max_age=settings.session_max_hours * 60 * 60,
        path="/api/v1/auth",
    )


async def _issue_tokens(db: AsyncSession, response: Response, user: User, auth_session: AuthSession) -> str:
    """Mint a rotated refresh token (cookie) + a fresh access token, both
    bound to `auth_session`."""
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            session_id=auth_session.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_token_expiry(auth_session.started_at),
        )
    )
    await db.commit()
    _set_refresh_cookie(response, raw_refresh)
    return create_access_token(user.id, user.role.value, auth_session.id)


async def _start_session(db: AsyncSession, response: Response, user: User) -> str:
    now = datetime.now(UTC)
    auth_session = AuthSession(user_id=user.id, started_at=now, last_active_at=now)
    db.add(auth_session)
    await db.flush()
    return await _issue_tokens(db, response, user, auth_session)


@router.post("/sign-up", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def sign_up(request: Request, body: SignUpRequest, db: AsyncSession = Depends(get_db)) -> User:
    existing = (
        await db.execute(select(User).where(or_(User.email == body.email, User.mobile_number == body.mobile_number)))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email or mobile number already exists.")

    user = User(
        full_name=body.full_name,
        email=body.email,
        mobile_number=body.mobile_number,
        password_hash=hash_password(body.password),
        role=Role.CUSTOMER,
        email_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/verify-otp", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: VerifyOtpRequest, db: AsyncSession = Depends(get_db)) -> None:
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="We couldn't find that account.")
    user.email_verified = True
    await db.commit()


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user = (
        await db.execute(select(User).where(or_(User.email == body.identifier, User.mobile_number == body.identifier)))
    ).scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email/mobile number or password.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email before signing in.")

    access_token = await _start_session(db, response, user)
    return {"accessToken": access_token, "user": user}


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("30/minute")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    token_hash = hash_refresh_token(raw_token)
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    expires_at = stored.expires_at if stored else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if stored is None or stored.revoked_at is not None or expires_at < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    user = (await db.execute(select(User).where(User.id == stored.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    # Legacy tokens (issued before sessions existed) have no session: force a
    # fresh login. Idle/absolute expiry is judged on the session, not the token.
    auth_session = (
        await db.execute(select(AuthSession).where(AuthSession.id == stored.session_id))
    ).scalar_one_or_none()
    if auth_session is None or not session_is_live(auth_session, now):
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = now
        stored.revoked_at = now
        await db.commit()
        response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session expired due to inactivity. Please sign in again.",
        )

    # Refreshing is NOT user activity (a background poll can trigger it): it
    # never extends last_active_at; only /auth/heartbeat does.
    stored.revoked_at = now
    access_token = await _issue_tokens(db, response, user, auth_session)
    return {"accessToken": access_token}


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def heartbeat(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Called by the browser only when the user actually interacts (mouse,
    keyboard, touch). This is the sole thing that extends the idle window."""
    request.state.auth_session.last_active_at = datetime.now(UTC)
    await db.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token:
        token_hash = hash_refresh_token(raw_token)
        stored = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if stored is not None:
            now = datetime.now(UTC)
            stored.revoked_at = now
            if stored.session_id is not None:
                auth_session = (
                    await db.execute(select(AuthSession).where(AuthSession.id == stored.session_id))
                ).scalar_one_or_none()
                if auth_session is not None and auth_session.revoked_at is None:
                    auth_session.revoked_at = now
            await db.commit()
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")

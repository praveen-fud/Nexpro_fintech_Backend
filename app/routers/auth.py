from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.enums import Role
from app.models.user import RefreshToken, User
from app.schemas.auth import LoginRequest, LoginResponse, RefreshResponse, SignUpRequest, VerifyOtpRequest
from app.schemas.user import UserResponse
from app.security.passwords import hash_password, verify_password
from app.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path="/api/v1/auth",
    )


async def _issue_session(db: AsyncSession, response: Response, user: User) -> str:
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_token_expiry(),
        )
    )
    await db.commit()
    _set_refresh_cookie(response, raw_refresh)
    return create_access_token(user.id, user.role.value)


@router.post("/sign-up", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def sign_up(body: SignUpRequest, db: AsyncSession = Depends(get_db)) -> User:
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
        # OTP verification is disabled for now (no real SMS/email provider
        # connected yet) — accounts are usable immediately. The /verify-otp
        # endpoint and login's email_verified check are left in place so
        # this can be re-enabled later by just flipping this back to False
        # and reinstating the frontend's OTP step.
        email_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/verify-otp", status_code=status.HTTP_204_NO_CONTENT)
async def verify_otp(body: VerifyOtpRequest, db: AsyncSession = Depends(get_db)) -> None:
    # Stage 1 simulation: any syntactically valid 6-digit code is accepted —
    # no real SMS/email OTP provider is connected yet.
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="We couldn't find that account.")
    user.email_verified = True
    await db.commit()


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user = (
        await db.execute(select(User).where(or_(User.email == body.identifier, User.mobile_number == body.identifier)))
    ).scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email/mobile number or password.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email before signing in.")

    access_token = await _issue_session(db, response, user)
    return {"accessToken": access_token, "user": user}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    token_hash = hash_refresh_token(raw_token)
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    # SQLite drops tzinfo on read-back even for a DateTime(timezone=True)
    # column (Postgres doesn't have this problem) — normalize before
    # comparing so this doesn't blow up with "can't compare offset-naive
    # and offset-aware datetimes" in local/SQLite dev.
    expires_at = stored.expires_at if stored else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if stored is None or stored.revoked_at is not None or expires_at < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    user = (await db.execute(select(User).where(User.id == stored.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")

    # Rotate: revoke the used refresh token and issue a new one.
    stored.revoked_at = now
    access_token = await _issue_session(db, response, user)
    return {"accessToken": access_token}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token:
        token_hash = hash_refresh_token(raw_token)
        stored = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if stored is not None:
            stored.revoked_at = datetime.now(UTC)
            await db.commit()
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")

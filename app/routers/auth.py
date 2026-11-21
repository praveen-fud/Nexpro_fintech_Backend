from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import enforce_subject_limit, login_failures, rate_limit
from app.models.enums import Role
from app.models.user import AuthSession, RefreshToken, User
from app.schemas.auth import LoginRequest, LoginResponse, RefreshResponse, SignUpRequest, VerifyOtpRequest
from app.schemas.user import UserResponse
from app.security.deps import get_current_user
from app.security.passwords import dummy_verify, hash_password, needs_rehash, verify_password
from app.security.sessions import session_is_live
from app.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

# One message for every "we are not signing you in" outcome. Distinct replies
# for "no such account", "wrong password" and "account deactivated" would each
# confirm whether an email or mobile number is registered here, which is the
# first step of a credential-stuffing run against a financial service.
_SIGN_IN_FAILED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email/mobile number or password."
)


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        max_age=settings.session_max_hours * 60 * 60,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.refresh_cookie_name,
        path="/api/v1/auth",
        domain=settings.cookie_domain,
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


async def _revoke_session(db: AsyncSession, session_id, now: datetime) -> None:
    """Kill a session and every refresh token issued under it."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


@router.post(
    "/sign-up",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("signup", "rate_limit_signup"))],
)
async def sign_up(request: Request, body: SignUpRequest, db: AsyncSession = Depends(get_db)) -> User:
    existing = (
        await db.execute(select(User).where(or_(User.email == body.email, User.mobile_number == body.mobile_number)))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or mobile number already exists.",
        )

    # Role is pinned here, never taken from the request body: self-registration
    # is for customers only. Operations/Super Admin come from app.create_admin.
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


@router.post(
    "/verify-otp",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
    dependencies=[Depends(rate_limit("verify_otp", "rate_limit_login"))],
)
async def verify_otp(request: Request, body: VerifyOtpRequest, db: AsyncSession = Depends(get_db)) -> None:
    """Dormant. Left in place for when a real SMS/email provider is wired up.

    It is disabled by default because it does NOT validate `code` against
    anything — switching it on without implementing real OTP delivery and
    verification would give any anonymous caller the ability to mark any
    address verified, and would confirm which addresses are registered.
    """
    if not settings.enable_otp_endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is not None:
        user.email_verified = True
        await db.commit()
    # Always 204, whether or not the address exists: the response must not
    # double as a "is this email registered?" oracle.


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(rate_limit("login", "rate_limit_login"))],
)
async def login(request: Request, body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    identifier = body.identifier.strip().lower()

    # Per-account lockout, on top of the per-IP budget above. A password spray
    # distributed over many IPs keeps every individual IP under its limit, but
    # still piles failures onto one account — this is what catches that.
    locked_for = login_failures.locked_for(
        identifier,
        max_failures=settings.login_max_failures,
        lockout_seconds=settings.login_lockout_seconds,
    )
    if locked_for:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Please try again later.",
            headers={"Retry-After": str(locked_for)},
        )

    # Also bound attempts per targeted account regardless of outcome.
    enforce_subject_limit(request, scope="login_subject", spec_attr="rate_limit_login", subject=identifier)

    user = (
        await db.execute(select(User).where(or_(User.email == body.identifier, User.mobile_number == body.identifier)))
    ).scalar_one_or_none()

    if user is None:
        # Burn an equivalent Argon2 verification so "no such account" takes the
        # same time as "wrong password". Without this the latency difference is
        # a reliable account-existence oracle.
        dummy_verify()
        login_failures.record_failure(identifier, lockout_seconds=settings.login_lockout_seconds)
        raise _SIGN_IN_FAILED

    if not verify_password(body.password, user.password_hash):
        login_failures.record_failure(identifier, lockout_seconds=settings.login_lockout_seconds)
        raise _SIGN_IN_FAILED

    # Deliberately the same response as a wrong password (see _SIGN_IN_FAILED).
    if not user.is_active or not user.email_verified:
        raise _SIGN_IN_FAILED

    # Credentials were correct: forget the failure history for this account.
    login_failures.reset(identifier)

    # Opportunistically upgrade hashes written under weaker Argon2 parameters.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    access_token = await _start_session(db, response, user)
    return {"accessToken": access_token, "user": user}


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    dependencies=[Depends(rate_limit("refresh", "rate_limit_refresh"))],
)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    expired = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again."
    )

    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise expired

    token_hash = hash_refresh_token(raw_token)
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if stored is None:
        raise expired

    # Reuse detection. Tokens are single-use and rotated, so a second
    # presentation of an already-rotated token means either a stolen cookie
    # being replayed or the legitimate client racing itself. We cannot tell
    # which, so we assume theft and tear down the whole session family —
    # the attacker's token and the victim's both stop working.
    if stored.revoked_at is not None:
        if stored.session_id is not None:
            await _revoke_session(db, stored.session_id, now)
            await db.commit()
        _clear_refresh_cookie(response)
        raise expired

    expires_at = stored.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or expires_at < now:
        raise expired

    user = (await db.execute(select(User).where(User.id == stored.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise expired

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
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session expired due to inactivity. Please sign in again.",
        )

    # Refreshing is NOT user activity (a background poll can trigger it): it
    # never extends last_active_at; only /auth/heartbeat does.
    stored.revoked_at = now
    access_token = await _issue_tokens(db, response, user, auth_session)
    return {"accessToken": access_token}


@router.post(
    "/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("heartbeat", "rate_limit_refresh"))],
)
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
                await _revoke_session(db, stored.session_id, now)
            await db.commit()
    _clear_refresh_cookie(response)

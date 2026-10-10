import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import KycStatus, Role
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.kyc import KycProfile
    from app.models.wallet import Wallet


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mobile_number: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.CUSTOMER)
    kyc_status: Mapped[KycStatus] = mapped_column(Enum(KycStatus, native_enum=False), default=KycStatus.NOT_STARTED)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    kyc_profile: Mapped["KycProfile | None"] = relationship(
        back_populates="user", uselist=False, foreign_keys="KycProfile.user_id"
    )
    wallet: Mapped["Wallet | None"] = relationship(back_populates="user", uselist=False)


class AuthSession(UUIDPrimaryKeyMixin, Base):
    """One login session. Access tokens carry its id (`sid`); the server
    rejects them once the session is idle too long, expired, or revoked.
    Refresh-token rotation keeps the same session."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """Rotating refresh tokens. We store a hash, never the raw token."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("auth_sessions.id"), index=True, nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import FundingMethod, FundingStatus, PaymentStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class FundingRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "funding_requests"

    request_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    method: Mapped[FundingMethod] = mapped_column(Enum(FundingMethod, native_enum=False))
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    wallet_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[FundingStatus] = mapped_column(Enum(FundingStatus, native_enum=False), default=FundingStatus.PENDING)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False), default=PaymentStatus.INITIATED
    )
    reference: Mapped[str] = mapped_column(String(64))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    proof_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    customer: Mapped["User"] = relationship(foreign_keys=[customer_id])
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(back_populates="funding_request")


class PaymentAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_attempts"

    funding_request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("funding_requests.id"))
    provider: Mapped[str] = mapped_column(String(40), default="MOCK")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, native_enum=False))
    masked_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    funding_request: Mapped["FundingRequest"] = relationship(back_populates="payment_attempts")


class IdempotencyRecord(UUIDPrimaryKeyMixin, Base):
    """
    Guards money-affecting endpoints (e.g. approve) against duplicate
    submission. A unique constraint on `key` means a second request with
    the same Idempotency-Key header fails fast before any ledger write.
    """

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

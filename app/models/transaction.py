import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import FundingMethod, TransactionStatus, TransactionType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Transaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transactions"

    transaction_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    funding_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("funding_requests.id"), nullable=True
    )
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType, native_enum=False))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, native_enum=False), default=TransactionStatus.PENDING
    )
    method: Mapped[FundingMethod | None] = mapped_column(Enum(FundingMethod, native_enum=False), nullable=True)
    reference: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))

    customer: Mapped["User"] = relationship(foreign_keys=[customer_id])

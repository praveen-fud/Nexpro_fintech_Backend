import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import LedgerDirection, LedgerStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Wallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Intentionally has NO balance column. Available/pending balances are
    always derived by summing WalletLedgerEntry rows — see
    services/wallet_service.py. This is the single most important
    invariant in the system: never add a mutable balance field here.
    """

    __tablename__ = "wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), unique=True)
    wallet_number: Mapped[str] = mapped_column(String(32), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    user: Mapped["User"] = relationship(back_populates="wallet")
    ledger_entries: Mapped[list["WalletLedgerEntry"]] = relationship(back_populates="wallet")


class WalletLedgerEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "wallet_ledger_entries"

    wallet_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("wallets.id"), index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(40))
    direction: Mapped[LedgerDirection] = mapped_column(Enum(LedgerDirection, native_enum=False))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[LedgerStatus] = mapped_column(Enum(LedgerStatus, native_enum=False), default=LedgerStatus.PENDING)
    reference: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    wallet: Mapped["Wallet"] = relationship(back_populates="ledger_entries")

from decimal import Decimal

from sqlalchemy import Boolean, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import FundingMethod
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeeRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Admin-configurable fee rule per funding method. The frontend never
    computes fees itself — it always calls /funding-requests/quote, which
    reads this table via services/fee_service.py.
    """

    __tablename__ = "fee_rules"

    method: Mapped[FundingMethod] = mapped_column(Enum(FundingMethod, native_enum=False), unique=True)
    fee_type: Mapped[str] = mapped_column(String(20), default="PERCENTAGE")  # FIXED | PERCENTAGE
    fixed_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    min_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    max_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PlatformLimit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single global row for Stage 1. Per-customer overrides can extend this later."""

    __tablename__ = "platform_limits"

    scope: Mapped[str] = mapped_column(String(20), unique=True, default="GLOBAL")
    per_transaction: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    daily: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    monthly: Mapped[Decimal] = mapped_column(Numeric(14, 2))

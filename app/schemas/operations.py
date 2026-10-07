from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models.enums import FundingMethod, KycStatus, TransactionStatus, TransactionType
from app.schemas.common import CamelModel


class FundingVolumeByDay(CamelModel):
    day: str
    amount: Decimal


class FundingByMethod(CamelModel):
    method: str
    value: int


class NeedsAttentionItem(CamelModel):
    label: str
    count: int
    to: str


class OperationsOverviewResponse(CamelModel):
    pending_funding: Decimal
    approved_today: int
    total_funding_volume: Decimal
    kyc_pending: int
    exceptions: int
    funding_volume_by_day: list[FundingVolumeByDay]
    funding_by_method: list[FundingByMethod]
    needs_attention: list[NeedsAttentionItem]


# ── Customers ──────────────────────────────────────────────────────────────────

class CustomerListItem(CamelModel):
    id: UUID
    full_name: str
    email: str
    mobile_number: str
    kyc_status: KycStatus
    is_active: bool
    created_at: datetime


class CustomerListResponse(CamelModel):
    items: list[CustomerListItem]
    total: int


class CustomerDetailResponse(CamelModel):
    id: UUID
    full_name: str
    email: str
    mobile_number: str
    kyc_status: KycStatus
    is_active: bool
    created_at: datetime
    wallet_number: str | None
    available_balance: Decimal | None
    pending_balance: Decimal | None


# ── Wallets ────────────────────────────────────────────────────────────────────

class WalletListItem(CamelModel):
    wallet_id: UUID
    wallet_number: str
    customer_id: UUID
    customer_name: str
    customer_email: str
    available_balance: Decimal
    pending_balance: Decimal
    currency: str
    created_at: datetime


class WalletListResponse(CamelModel):
    items: list[WalletListItem]
    total: int


# ── Transactions ───────────────────────────────────────────────────────────────

class TransactionListItem(CamelModel):
    id: UUID
    transaction_number: str
    customer_id: UUID
    customer_name: str
    type: TransactionType
    amount: Decimal
    fee: Decimal
    status: TransactionStatus
    method: FundingMethod | None
    reference: str
    description: str
    created_at: datetime


class TransactionListResponse(CamelModel):
    items: list[TransactionListItem]
    total: int


class InboxItem(CamelModel):
    kind: str  # FUNDING | KYC
    id: UUID
    reference: str
    customer_id: UUID
    customer_name: str
    customer_email: str
    title: str
    detail: str
    amount: Decimal | None = None
    status: str
    needs_action: bool
    submitted_at: datetime
    waiting_minutes: int
    flags: list[str] = []


class InboxSummary(CamelModel):
    awaiting_action: int
    funding_awaiting: int
    kyc_awaiting: int
    oldest_waiting_minutes: int


class InboxResponse(CamelModel):
    summary: InboxSummary
    items: list[InboxItem]

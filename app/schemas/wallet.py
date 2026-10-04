import uuid
from datetime import datetime
from decimal import Decimal

from app.models.enums import LedgerDirection
from app.schemas.common import CamelModel


class WalletResponse(CamelModel):
    id: uuid.UUID
    wallet_id: str
    available_balance: Decimal
    pending_balance: Decimal
    currency: str
    updated_at: datetime


class WalletLedgerEntryResponse(CamelModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    transaction_id: uuid.UUID | None
    entry_type: str
    direction: LedgerDirection
    amount: Decimal
    status: str
    reference: str
    created_at: datetime

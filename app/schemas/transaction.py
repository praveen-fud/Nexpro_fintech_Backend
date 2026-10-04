import uuid
from datetime import datetime
from decimal import Decimal

from app.models.enums import FundingMethod, TransactionStatus, TransactionType
from app.schemas.common import CamelModel


class TransactionResponse(CamelModel):
    id: uuid.UUID
    transaction_number: str
    type: TransactionType
    amount: Decimal
    fee: Decimal
    status: TransactionStatus
    method: FundingMethod | None
    reference: str
    description: str
    created_at: datetime
    updated_at: datetime

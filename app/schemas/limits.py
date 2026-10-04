from decimal import Decimal

from app.schemas.common import CamelModel


class LimitsResponse(CamelModel):
    per_transaction: Decimal
    daily: Decimal
    monthly: Decimal

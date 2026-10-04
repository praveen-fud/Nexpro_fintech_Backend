from decimal import Decimal

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

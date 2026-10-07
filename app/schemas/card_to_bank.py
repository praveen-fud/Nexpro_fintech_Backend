from decimal import Decimal

from app.schemas.common import CamelModel


class CardToBankQuoteResponse(CamelModel):
    amount_to_bank: Decimal
    gateway_charge: Decimal
    commission: Decimal
    payout_fee: Decimal
    gst: Decimal
    total_fees: Decimal
    card_total: Decimal


class CardToBankRateCardResponse(CamelModel):
    gateway_rate: Decimal
    commission_rate: Decimal
    min_commission: Decimal
    payout_fee: Decimal
    gst_rate: Decimal
    min_amount: Decimal
    max_amount: Decimal
    payout_eta: str

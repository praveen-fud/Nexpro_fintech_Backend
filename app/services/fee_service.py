from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import FeeRule
from app.models.enums import FundingMethod


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


async def get_fee_rule(db: AsyncSession, method: FundingMethod) -> FeeRule | None:
    return (await db.execute(select(FeeRule).where(FeeRule.method == method))).scalar_one_or_none()


async def calculate_fee(db: AsyncSession, method: FundingMethod, amount: Decimal) -> Decimal:
    """
    The single source of truth for funding fees. The frontend never computes
    this — it always calls /funding-requests/quote. Fee is additive: the
    customer pays amount + fee, and the full requested amount is credited to
    the wallet (see FundingQuoteResponse).
    """
    rule = await get_fee_rule(db, method)
    if rule is None or not rule.is_enabled:
        return Decimal("0")

    if rule.fee_type == "FIXED":
        fee = rule.fixed_amount
    else:
        fee = amount * (rule.percentage / Decimal("100"))

    fee = _round(fee)
    if rule.min_fee and fee < rule.min_fee:
        fee = rule.min_fee
    if rule.max_fee and fee > rule.max_fee:
        fee = rule.max_fee
    return fee

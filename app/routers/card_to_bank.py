from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.enums import KycStatus, Role
from app.models.user import User
from app.schemas.card_to_bank import CardToBankQuoteResponse, CardToBankRateCardResponse
from app.security.deps import require_roles
from app.services import card_to_bank_service as pricing

router = APIRouter(prefix="/card-to-bank", tags=["card-to-bank"])


def _require_approved(user: User) -> None:
    if user.kyc_status != KycStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Complete identity verification to use this service."
        )


@router.get("/rate-card", response_model=CardToBankRateCardResponse)
async def rate_card(user: User = Depends(require_roles(Role.CUSTOMER))) -> CardToBankRateCardResponse:
    _require_approved(user)
    return CardToBankRateCardResponse(
        gateway_rate=pricing.GATEWAY_RATE,
        commission_rate=pricing.COMMISSION_RATE,
        min_commission=pricing.MIN_COMMISSION,
        payout_fee=pricing.PAYOUT_FEE,
        gst_rate=pricing.GST_RATE,
        min_amount=pricing.MIN_AMOUNT,
        max_amount=pricing.MAX_AMOUNT,
        payout_eta=pricing.PAYOUT_ETA,
    )


@router.get("/quote", response_model=CardToBankQuoteResponse)
async def quote(amount: Decimal, user: User = Depends(require_roles(Role.CUSTOMER))) -> CardToBankQuoteResponse:
    _require_approved(user)
    if amount < pricing.MIN_AMOUNT or amount > pricing.MAX_AMOUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amount must be between ₹{pricing.MIN_AMOUNT:,.0f} and ₹{pricing.MAX_AMOUNT:,.0f}.",
        )
    return CardToBankQuoteResponse(**pricing.calculate(amount))

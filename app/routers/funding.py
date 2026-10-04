import json
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.database import get_db
from app.models.enums import FundingMethod, KycStatus, Role
from app.models.funding import FundingRequest
from app.models.user import User
from app.schemas.funding import BeneficiaryInfoResponse, FundingQuoteResponse, FundingRequestResponse
from app.security.deps import require_roles
from app.services import fee_service, numbering_service
from app.services.file_storage import save_upload
from app.services.funding_service import create_funding_request

router = APIRouter(prefix="/funding-requests", tags=["funding"])

MIN_AMOUNT = Decimal("100")
MAX_AMOUNT = Decimal("200000")


def _to_response(fr: FundingRequest, customer_name: str) -> FundingRequestResponse:
    return FundingRequestResponse(
        id=fr.id,
        request_number=fr.request_number,
        customer_id=fr.customer_id,
        customer_name=customer_name,
        method=fr.method,
        requested_amount=fr.requested_amount,
        fee=fr.fee,
        wallet_credit=fr.wallet_credit,
        status=fr.status,
        payment_status=fr.payment_status,
        reference=fr.reference,
        assigned_to=None,
        created_at=fr.created_at,
        updated_at=fr.updated_at,
    )


@router.get("/quote", response_model=FundingQuoteResponse)
async def get_quote(
    amount: Decimal, method: FundingMethod, db: AsyncSession = Depends(get_db)
) -> FundingQuoteResponse:
    if amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter an amount greater than zero.")
    fee = await fee_service.calculate_fee(db, method, amount)
    return FundingQuoteResponse(
        requested_amount=amount, fee=fee, wallet_credit=amount, total_payment=amount + fee
    )


@router.get("/bank-transfer/beneficiary", response_model=BeneficiaryInfoResponse)
async def get_beneficiary(
    user: User = Depends(require_roles(Role.CUSTOMER)),
) -> BeneficiaryInfoResponse:
    return BeneficiaryInfoResponse(
        account_name="Nexpro Fintech Pvt Ltd",
        account_number_masked="XXXXXXXX4821",
        ifsc="NXPR0000001",
        reference=numbering_service.new_customer_reference(user.mobile_number),
    )


@router.post("", response_model=FundingRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_request(
    request: Request,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    if user.kyc_status != KycStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Complete identity verification before funding your wallet.",
        )

    content_type = request.headers.get("content-type", "")
    proof_path: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        method_raw = form.get("method")
        amount_raw = form.get("amount")
        payment_details_raw = form.get("paymentDetails", "{}")
        proof = form.get("proof")
        if isinstance(proof, StarletteUploadFile):
            proof_path = await save_upload(proof, subdir=f"funding-proof/{user.id}")
        payment_details = json.loads(payment_details_raw) if isinstance(payment_details_raw, str) else {}
    else:
        body = await request.json()
        method_raw = body.get("method")
        amount_raw = body.get("amount")
        payment_details = body.get("paymentDetails", {})

    try:
        method = FundingMethod(method_raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported funding method.") from exc

    try:
        amount = Decimal(str(amount_raw))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid amount.") from exc

    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amount must be between ₹{MIN_AMOUNT:,.0f} and ₹{MAX_AMOUNT:,.0f}.",
        )

    funding_request = await create_funding_request(
        db,
        customer=user,
        method=method,
        amount=amount,
        payment_details=payment_details,
        proof_file_path=proof_path,
    )
    return _to_response(funding_request, user.full_name)


@router.get("/{funding_request_id}", response_model=FundingRequestResponse)
async def get_request(
    funding_request_id: uuid.UUID,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    fr = (
        await db.execute(select(FundingRequest).where(FundingRequest.id == funding_request_id))
    ).scalar_one_or_none()
    if fr is None or fr.customer_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funding request not found.")
    return _to_response(fr, user.full_name)

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.models.enums import FundingMethod, KycStatus, Role
from app.models.funding import FundingRequest
from app.models.user import User
from app.schemas.funding import (
    BeneficiaryInfoResponse,
    CardOrderBody,
    CardOrderResponse,
    CardVerifyBody,
    FundingQuoteResponse,
    FundingRequestResponse,
    PublicUpiPaymentResponse,
    UpiPayeeResponse,
    UpiPaymentLinkBody,
    UpiPaymentLinkResponse,
)
from app.security.deps import require_roles
from app.services import fee_service, numbering_service, razorpay_service
from app.services.file_storage import save_upload
from app.services.funding_service import capture_card_payment, create_funding_request

router = APIRouter(prefix="/funding-requests", tags=["funding"])
# Unauthenticated: lets someone other than the customer open a payment link.
public_router = APIRouter(prefix="/public", tags=["public"])

PAY_LINK_TTL = timedelta(hours=24)

settings = get_settings()

# UPI/IMPS UTRs are 12 digits; NEFT/RTGS references are 16-22 alphanumerics.
UTR_PATTERN = re.compile(r"^[A-Z0-9]{12,22}$")

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
        utr=fr.utr,
        has_proof=bool(fr.proof_file_path),
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


def _payee_or_503(value: str, demo: str, label: str) -> tuple[str, bool]:
    """Real company payee details come from settings. Only development may fall
    back to demo values — in production a missing value must fail loudly rather
    than show customers a wrong account to pay."""
    if value:
        return value, False
    if settings.environment == "development":
        return demo, True
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{label} is not configured.")


@router.get("/upi/payee", response_model=UpiPayeeResponse)
async def get_upi_payee(user: User = Depends(require_roles(Role.CUSTOMER))) -> UpiPayeeResponse:
    upi_id, demo = _payee_or_503(settings.payee_upi_id, "nexpro@demobank", "UPI payee")
    name, _ = _payee_or_503(settings.payee_upi_name, "Nexpro Fintech Pvt Ltd", "UPI payee name")
    return UpiPayeeResponse(
        upi_id=upi_id,
        payee_name=name,
        reference=numbering_service.new_customer_reference(user.mobile_number),
        is_demo=demo,
    )


def _pay_link_key() -> str:
    # Purpose-bound key: a payment-link token can never validate as a login token
    # (or vice-versa) even though both derive from the same server secret.
    return f"{settings.jwt_secret}:upi-pay-link"


@router.post("/upi/payment-link", response_model=UpiPaymentLinkResponse)
async def create_upi_payment_link(
    body: UpiPaymentLinkBody,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> UpiPaymentLinkResponse:
    """Makes a signed, expiring link so a third person (family, employer…) can pay
    on the customer's behalf. Nothing is stored; the amount, note and expiry are
    sealed inside the token, so the link can't be edited to change what's paid.
    It exposes only our own payee details — nothing about the customer."""
    _require_kyc_and_range(user, body.amount)
    if body.reference[4:9] != user.mobile_number[-5:]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment reference.")
    fee = await fee_service.calculate_fee(db, FundingMethod.UPI, body.amount)
    expires_at = datetime.now(UTC) + PAY_LINK_TTL
    token = jwt.encode(
        {"typ": "upi_link", "amt": str(body.amount + fee), "ref": body.reference, "exp": expires_at},
        _pay_link_key(),
        algorithm="HS256",
    )
    return UpiPaymentLinkResponse(token=token, expires_at=expires_at)


@public_router.get("/upi-payment/{token}", response_model=PublicUpiPaymentResponse)
@limiter.limit("30/minute")
async def open_upi_payment_link(request: Request, token: str) -> PublicUpiPaymentResponse:
    try:
        claims = jwt.decode(token, _pay_link_key(), algorithms=["HS256"])
        if claims.get("typ") != "upi_link":
            raise jwt.InvalidTokenError
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This payment link has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This payment link is not valid.") from exc

    upi_id, demo = _payee_or_503(settings.payee_upi_id, "nexpro@demobank", "UPI payee")
    name, _ = _payee_or_503(settings.payee_upi_name, "Nexpro Fintech Pvt Ltd", "UPI payee name")
    return PublicUpiPaymentResponse(
        upi_id=upi_id,
        payee_name=name,
        amount=Decimal(claims["amt"]),
        reference=claims["ref"],
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        is_demo=demo,
    )


@router.get("/bank-transfer/beneficiary", response_model=BeneficiaryInfoResponse)
async def get_beneficiary(
    user: User = Depends(require_roles(Role.CUSTOMER)),
) -> BeneficiaryInfoResponse:
    number, demo = _payee_or_503(settings.payee_bank_account_number, "000000000000", "Bank account")
    ifsc, _ = _payee_or_503(settings.payee_bank_ifsc, "DEMO0000001", "Bank IFSC")
    name, _ = _payee_or_503(settings.payee_bank_account_name, "Nexpro Fintech Pvt Ltd", "Bank account name")
    bank, _ = _payee_or_503(settings.payee_bank_name, "Demo Bank", "Bank name")
    return BeneficiaryInfoResponse(
        account_name=name,
        bank_name=bank,
        account_number=number,
        ifsc=ifsc,
        reference=numbering_service.new_customer_reference(user.mobile_number),
        is_demo=demo,
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

    if method == FundingMethod.CREDIT_CARD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Card payments are made through the secure card checkout."
        )

    utr: str | None = None
    if method in (FundingMethod.UPI, FundingMethod.BANK_TRANSFER):
        utr = str(payment_details.get("referenceNumber", "")).strip().upper()
        if not UTR_PATTERN.fullmatch(utr):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a valid transaction ID (UTR) — 12 to 22 letters/digits.",
            )
        # A UPI payment is verified from the screenshot + UTR; bank transfers
        # may omit the screenshot (reconciliation is the real check there).
        if method == FundingMethod.UPI and proof_path is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload the payment screenshot so we can verify your UPI payment.",
            )

    funding_request = await create_funding_request(
        db,
        customer=user,
        method=method,
        amount=amount,
        payment_details=payment_details,
        proof_file_path=proof_path,
        utr=utr,
    )
    return _to_response(funding_request, user.full_name)


def _require_kyc_and_range(user: User, amount: Decimal) -> None:
    if user.kyc_status != KycStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Complete identity verification before funding your wallet.",
        )
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amount must be between ₹{MIN_AMOUNT:,.0f} and ₹{MAX_AMOUNT:,.0f}.",
        )


@router.get("/card/config")
async def card_config(user: User = Depends(require_roles(Role.CUSTOMER))) -> dict[str, bool]:
    return {"enabled": razorpay_service.is_configured()}


@router.post("/card/order", response_model=CardOrderResponse)
async def create_card_order(
    body: CardOrderBody,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> CardOrderResponse:
    """Step 1 of card payment. The amount charged (requested + fee) is fixed
    HERE, on the server, and baked into the gateway order — the browser can't
    change what gets charged."""
    _require_kyc_and_range(user, body.amount)
    fee = await fee_service.calculate_fee(db, FundingMethod.CREDIT_CARD, body.amount)
    order = await razorpay_service.create_order(
        amount_paise=razorpay_service.to_paise(body.amount + fee),
        receipt=numbering_service.new_request_number(),
        notes={"customerId": str(user.id), "requestedAmount": str(body.amount)},
    )
    return CardOrderResponse(
        order_id=order["id"],
        key_id=settings.razorpay_key_id,
        amount_paise=order["amount"],
        requested_amount=body.amount,
        fee=fee,
    )


async def _credit_if_genuinely_paid(db: AsyncSession, *, order_id: str, payment_id: str) -> FundingRequest | None:
    """Re-checks everything with the gateway itself (never trusting the browser)
    and only then credits. Returns None when the payment isn't captured yet."""
    order = await razorpay_service.fetch_order(order_id)
    payment = await razorpay_service.fetch_payment(payment_id)

    if payment.get("order_id") != order_id or payment.get("amount") != order.get("amount"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment does not match this order.")
    if payment.get("status") != "captured":
        return None

    notes = order.get("notes") or {}
    try:
        customer_id = uuid.UUID(str(notes.get("customerId")))
        amount = Decimal(str(notes.get("requestedAmount")))
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unrecognised payment order.") from exc

    customer = (await db.execute(select(User).where(User.id == customer_id))).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unrecognised payment order.")

    # The charged amount must equal what OUR fee rules say it should be.
    fee = await fee_service.calculate_fee(db, FundingMethod.CREDIT_CARD, amount)
    if order.get("amount") != razorpay_service.to_paise(amount + fee):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment amount mismatch.")

    return await capture_card_payment(db, customer=customer, amount=amount, order_id=order_id, payment_id=payment_id)


@router.post("/card/verify", response_model=FundingRequestResponse)
async def verify_card_payment(
    body: CardVerifyBody,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    """Step 2: the browser reports a finished payment. We verify the gateway
    signature, then confirm with the gateway before crediting the wallet."""
    if not razorpay_service.verify_checkout_signature(body.order_id, body.payment_id, body.signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment could not be verified.")

    order = await razorpay_service.fetch_order(body.order_id)
    if (order.get("notes") or {}).get("customerId") != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found.")

    funding_request = await _credit_if_genuinely_paid(db, order_id=body.order_id, payment_id=body.payment_id)
    if funding_request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your payment is still being confirmed. Your wallet will be credited automatically once it is.",
        )
    return _to_response(funding_request, user.full_name)


@router.post("/card/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Safety net for customers who pay then close the tab before step 2.
    Authenticated by the webhook signature, not by a login."""
    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if not razorpay_service.verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature.")

    event = json.loads(raw)
    if event.get("event") == "payment.captured":
        entity = event.get("payload", {}).get("payment", {}).get("entity", {})
        if entity.get("id") and entity.get("order_id"):
            await _credit_if_genuinely_paid(db, order_id=entity["order_id"], payment_id=entity["id"])
    return {"status": "ok"}


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

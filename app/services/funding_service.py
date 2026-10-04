import uuid
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    FUNDING_STATUS_TRANSITIONS,
    FundingMethod,
    FundingStatus,
    PaymentStatus,
    TransactionStatus,
    TransactionType,
)
from app.models.funding import FundingRequest, PaymentAttempt
from app.models.transaction import Transaction
from app.models.user import User
from app.services import audit_service, fee_service, numbering_service, wallet_service

METHOD_LABELS = {
    FundingMethod.CREDIT_CARD: "Credit Card",
    FundingMethod.UPI: "UPI",
    FundingMethod.BANK_TRANSFER: "Bank Transfer",
}


def _ensure_transition(current: FundingStatus, target: FundingStatus) -> None:
    allowed = FUNDING_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This request is already {current.value.replace('_', ' ').lower()} and cannot be changed again.",
        )


async def create_funding_request(
    db: AsyncSession,
    *,
    customer: User,
    method: FundingMethod,
    amount: Decimal,
    payment_details: dict[str, Any],
    proof_file_path: str | None = None,
) -> FundingRequest:
    fee = await fee_service.calculate_fee(db, method, amount)
    wallet_credit = amount  # Fee is additive — the full requested amount is credited.
    reference = payment_details.get("reference") or numbering_service.new_customer_reference(customer.mobile_number)

    funding_request = FundingRequest(
        request_number=numbering_service.new_request_number(),
        customer_id=customer.id,
        method=method,
        requested_amount=amount,
        fee=fee,
        wallet_credit=wallet_credit,
        status=FundingStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        reference=reference,
        proof_file_path=proof_file_path,
    )
    db.add(funding_request)
    await db.flush()

    transaction = Transaction(
        transaction_number=numbering_service.new_transaction_number(),
        customer_id=customer.id,
        funding_request_id=funding_request.id,
        type=TransactionType.FUNDING,
        amount=amount,
        fee=fee,
        status=TransactionStatus.PENDING,
        method=method,
        reference=reference,
        description=f"Wallet Funding via {METHOD_LABELS[method]}",
    )
    db.add(transaction)
    await db.flush()

    wallet = await wallet_service.get_or_create_wallet(db, customer.id)
    await wallet_service.post_pending_credit(
        db,
        wallet_id=wallet.id,
        amount=wallet_credit,
        entry_type="FUNDING_CREDIT",
        reference=reference,
        transaction_id=transaction.id,
    )

    masked_reference = (
        payment_details.get("maskedCard") or payment_details.get("upiId") or payment_details.get("referenceNumber")
    )
    db.add(
        PaymentAttempt(
            funding_request_id=funding_request.id,
            provider="MOCK",
            status=PaymentStatus.PENDING,
            masked_reference=masked_reference,
            details=payment_details,
        )
    )

    await db.commit()
    await db.refresh(funding_request)
    return funding_request


async def mark_under_review(db: AsyncSession, funding_request: FundingRequest) -> FundingRequest:
    """Simulates Operations picking up a request: the first time it's opened
    in the queue, PENDING -> UNDER_REVIEW."""
    if funding_request.status == FundingStatus.PENDING:
        funding_request.status = FundingStatus.UNDER_REVIEW
        await db.commit()
        await db.refresh(funding_request)
    return funding_request


async def _get_transaction_for_request(db: AsyncSession, funding_request_id: uuid.UUID) -> Transaction:
    txn = (
        await db.execute(select(Transaction).where(Transaction.funding_request_id == funding_request_id))
    ).scalar_one()
    return txn


async def approve_funding_request(db: AsyncSession, funding_request: FundingRequest, operator: User) -> FundingRequest:
    before_status = funding_request.status
    _ensure_transition(before_status, FundingStatus.APPROVED)

    transaction = await _get_transaction_for_request(db, funding_request.id)

    funding_request.status = FundingStatus.APPROVED
    funding_request.payment_status = PaymentStatus.CAPTURED
    funding_request.reviewed_by = operator.id
    transaction.status = TransactionStatus.COMPLETED

    await wallet_service.post_entry_for_transaction(db, transaction.id)

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="FUNDING_APPROVED",
        resource_type="FundingRequest",
        resource_id=funding_request.id,
        before_state={"status": before_status.value},
        after_state={"status": FundingStatus.APPROVED.value, "walletCredit": str(funding_request.wallet_credit)},
    )

    await db.commit()
    await db.refresh(funding_request)
    return funding_request


async def reject_funding_request(
    db: AsyncSession, funding_request: FundingRequest, operator: User, reason: str
) -> FundingRequest:
    before_status = funding_request.status
    _ensure_transition(before_status, FundingStatus.REJECTED)

    transaction = await _get_transaction_for_request(db, funding_request.id)

    funding_request.status = FundingStatus.REJECTED
    funding_request.payment_status = PaymentStatus.FAILED
    funding_request.review_notes = reason
    funding_request.reviewed_by = operator.id
    transaction.status = TransactionStatus.FAILED

    await wallet_service.reverse_entry_for_transaction(db, transaction.id)

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="FUNDING_REJECTED",
        resource_type="FundingRequest",
        resource_id=funding_request.id,
        before_state={"status": before_status.value},
        after_state={"status": FundingStatus.REJECTED.value},
        reason=reason,
    )

    await db.commit()
    await db.refresh(funding_request)
    return funding_request


async def request_additional_information(
    db: AsyncSession, funding_request: FundingRequest, operator: User, message: str
) -> FundingRequest:
    before_status = funding_request.status
    _ensure_transition(before_status, FundingStatus.ADDITIONAL_INFORMATION_REQUIRED)

    funding_request.status = FundingStatus.ADDITIONAL_INFORMATION_REQUIRED
    funding_request.review_notes = message
    funding_request.reviewed_by = operator.id

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="FUNDING_INFO_REQUESTED",
        resource_type="FundingRequest",
        resource_id=funding_request.id,
        before_state={"status": before_status.value},
        after_state={"status": FundingStatus.ADDITIONAL_INFORMATION_REQUIRED.value},
        reason=message,
    )

    await db.commit()
    await db.refresh(funding_request)
    return funding_request

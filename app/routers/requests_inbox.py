"""One inbox for everything a customer has asked us to verify.

Funding requests and KYC submissions are listed together, oldest-waiting first,
so Operations and Super Admin never have to hunt through separate queues. New
request types (e.g. card-to-bank payouts) plug in by adding another source below.
"""

from datetime import UTC, datetime
from enum import Enum

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import FundingMethod, FundingStatus, KycStatus, Role
from app.models.funding import FundingRequest
from app.models.kyc import KycProfile
from app.models.user import User
from app.schemas.operations import InboxItem, InboxResponse, InboxSummary
from app.security.deps import require_roles

router = APIRouter(prefix="/operations", tags=["operations"])

OPEN_FUNDING = (FundingStatus.PENDING, FundingStatus.UNDER_REVIEW)
OPEN_KYC = (KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW)
LARGE_AMOUNT = 100000

METHOD_LABEL = {
    FundingMethod.CREDIT_CARD: "Credit Card",
    FundingMethod.UPI: "UPI",
    FundingMethod.BANK_TRANSFER: "Bank Transfer",
}


class Kind(str, Enum):
    ALL = "ALL"
    FUNDING = "FUNDING"
    KYC = "KYC"


class State(str, Enum):
    NEEDS_ACTION = "NEEDS_ACTION"
    ALL = "ALL"
    DONE = "DONE"


def _minutes_since(moment: datetime, now: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(int((now - moment).total_seconds() // 60), 0)


def _funding_flags(fr: FundingRequest) -> list[str]:
    flags: list[str] = []
    if fr.requested_amount > LARGE_AMOUNT:
        flags.append("Large amount")
    if fr.method in (FundingMethod.UPI, FundingMethod.BANK_TRANSFER):
        if not fr.proof_file_path:
            flags.append("No proof")
        if not fr.utr:
            flags.append("No UTR")
    if fr.status == FundingStatus.ADDITIONAL_INFORMATION_REQUIRED:
        flags.append("Info requested")
    return flags


@router.get("/requests", response_model=InboxResponse)
async def list_requests(
    kind: Kind = Kind.ALL,
    state: State = State.NEEDS_ACTION,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    user: User = Depends(require_roles(Role.OPERATIONS, Role.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InboxResponse:
    now = datetime.now(UTC)
    pattern = f"%{search}%" if search else None
    items: list[InboxItem] = []

    if kind in (Kind.ALL, Kind.FUNDING):
        q = select(FundingRequest, User).join(User, User.id == FundingRequest.customer_id)
        if state == State.NEEDS_ACTION:
            q = q.where(FundingRequest.status.in_(OPEN_FUNDING))
        elif state == State.DONE:
            q = q.where(FundingRequest.status.not_in(OPEN_FUNDING))
        if pattern:
            q = q.where(
                or_(
                    FundingRequest.request_number.ilike(pattern),
                    FundingRequest.utr.ilike(pattern),
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                    User.mobile_number.ilike(pattern),
                )
            )
        for fr, customer in (await db.execute(q.order_by(FundingRequest.created_at.desc()).limit(limit))).all():
            items.append(
                InboxItem(
                    kind="FUNDING",
                    id=fr.id,
                    reference=fr.request_number,
                    customer_id=customer.id,
                    customer_name=customer.full_name,
                    customer_email=customer.email,
                    title=f"Add money · {METHOD_LABEL[fr.method]}",
                    detail=f"UTR {fr.utr}" if fr.utr else f"Ref {fr.reference}",
                    amount=fr.requested_amount,
                    status=fr.status.value,
                    needs_action=fr.status in OPEN_FUNDING,
                    submitted_at=fr.created_at,
                    waiting_minutes=_minutes_since(fr.created_at, now),
                    flags=_funding_flags(fr),
                )
            )

    if kind in (Kind.ALL, Kind.KYC):
        q = (
            select(KycProfile, User)
            .join(User, User.id == KycProfile.user_id)
            .where(KycProfile.status != KycStatus.NOT_STARTED)
        )
        if state == State.NEEDS_ACTION:
            q = q.where(KycProfile.status.in_(OPEN_KYC))
        elif state == State.DONE:
            q = q.where(KycProfile.status.not_in(OPEN_KYC))
        if pattern:
            q = q.where(
                or_(User.full_name.ilike(pattern), User.email.ilike(pattern), User.mobile_number.ilike(pattern))
            )
        for profile, customer in (await db.execute(q.order_by(KycProfile.created_at.desc()).limit(limit))).all():
            submitted = profile.submitted_at or profile.created_at
            items.append(
                InboxItem(
                    kind="KYC",
                    id=profile.id,
                    reference="KYC",
                    customer_id=customer.id,
                    customer_name=customer.full_name,
                    customer_email=customer.email,
                    title="Identity verification (KYC)",
                    detail=f"Mobile {customer.mobile_number}",
                    status=profile.status.value,
                    needs_action=profile.status in OPEN_KYC,
                    submitted_at=submitted,
                    waiting_minutes=_minutes_since(submitted, now),
                    flags=["Info requested"] if profile.status == KycStatus.ADDITIONAL_INFORMATION_REQUIRED else [],
                )
            )

    # Open work first, longest-waiting on top; finished items newest first.
    items.sort(key=lambda i: (0, -i.waiting_minutes) if i.needs_action else (1, i.waiting_minutes))

    funding_open = (
        await db.execute(select(func.count(FundingRequest.id)).where(FundingRequest.status.in_(OPEN_FUNDING)))
    ).scalar_one()
    kyc_open = (
        await db.execute(select(func.count(KycProfile.id)).where(KycProfile.status.in_(OPEN_KYC)))
    ).scalar_one()
    oldest_funding = (
        await db.execute(select(func.min(FundingRequest.created_at)).where(FundingRequest.status.in_(OPEN_FUNDING)))
    ).scalar_one()
    oldest_kyc = (
        await db.execute(
            select(func.min(func.coalesce(KycProfile.submitted_at, KycProfile.created_at))).where(
                KycProfile.status.in_(OPEN_KYC)
            )
        )
    ).scalar_one()
    waits = [_minutes_since(m, now) for m in (oldest_funding, oldest_kyc) if m is not None]

    return InboxResponse(
        summary=InboxSummary(
            awaiting_action=funding_open + kyc_open,
            funding_awaiting=funding_open,
            kyc_awaiting=kyc_open,
            oldest_waiting_minutes=max(waits, default=0),
        ),
        items=items[:limit],
    )

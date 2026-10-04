import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import FundingMethod, FundingStatus, KycStatus, Role
from app.models.funding import FundingRequest, IdempotencyRecord, PaymentAttempt
from app.models.kyc import KycDocument, KycProfile
from app.models.user import User
from app.schemas.funding import (
    FundingCustomerSummary,
    FundingDetailResponse,
    FundingRequestResponse,
    RejectFundingRequestBody,
    RequestInformationBody,
)
from app.schemas.kyc import (
    KycBankAccountResponse,
    KycCustomerSummary,
    KycDocumentResponse,
    KycPersonalInfoResponse,
    KycProfileResponse,
    KycQueueItemResponse,
    KycRejectBody,
    KycRequestInformationBody,
    KycReviewDetailResponse,
)
from app.schemas.operations import FundingByMethod, FundingVolumeByDay, NeedsAttentionItem, OperationsOverviewResponse
from app.security.deps import require_roles
from app.services.checklist_service import build_verification_checklist
from app.services.funding_service import approve_funding_request, mark_under_review, reject_funding_request, request_additional_information
from app.services.kyc_service import approve_kyc, reject_kyc, request_kyc_additional_information

router = APIRouter(prefix="/operations", tags=["operations"])

OPERATIONS_ROLES = (Role.OPERATIONS, Role.SUPER_ADMIN)


def _request_to_response(fr: FundingRequest, customer_name: str) -> FundingRequestResponse:
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


@router.get("/overview", response_model=OperationsOverviewResponse)
async def get_overview(
    user: User = Depends(require_roles(*OPERATIONS_ROLES)), db: AsyncSession = Depends(get_db)
) -> OperationsOverviewResponse:
    pending_funding = (
        await db.execute(
            select(func.coalesce(func.sum(FundingRequest.requested_amount), 0)).where(
                FundingRequest.status.in_([FundingStatus.PENDING, FundingStatus.UNDER_REVIEW])
            )
        )
    ).scalar_one()

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    approved_today = (
        await db.execute(
            select(func.count(FundingRequest.id)).where(
                FundingRequest.status == FundingStatus.APPROVED, FundingRequest.updated_at >= today_start
            )
        )
    ).scalar_one()

    total_volume = (
        await db.execute(
            select(func.coalesce(func.sum(FundingRequest.requested_amount), 0)).where(
                FundingRequest.status == FundingStatus.APPROVED
            )
        )
    ).scalar_one()

    kyc_pending = (
        await db.execute(
            select(func.count(User.id)).where(
                User.kyc_status.in_([KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW])
            )
        )
    ).scalar_one()

    exceptions = (
        await db.execute(
            select(func.count(FundingRequest.id)).where(
                FundingRequest.status == FundingStatus.ADDITIONAL_INFORMATION_REQUIRED
            )
        )
    ).scalar_one()

    volume_by_day: list[FundingVolumeByDay] = []
    for offset in range(6, -1, -1):
        day_start = today_start - timedelta(days=offset)
        day_end = day_start + timedelta(days=1)
        day_sum = (
            await db.execute(
                select(func.coalesce(func.sum(FundingRequest.requested_amount), 0)).where(
                    FundingRequest.created_at >= day_start, FundingRequest.created_at < day_end
                )
            )
        ).scalar_one()
        volume_by_day.append(FundingVolumeByDay(day=day_start.strftime("%a"), amount=Decimal(day_sum)))

    by_method: list[FundingByMethod] = []
    for method in FundingMethod:
        count = (
            await db.execute(select(func.count(FundingRequest.id)).where(FundingRequest.method == method))
        ).scalar_one()
        if count:
            by_method.append(FundingByMethod(method=method.value.replace("_", " ").title(), value=count))

    pending_count = (
        await db.execute(
            select(func.count(FundingRequest.id)).where(
                FundingRequest.status.in_([FundingStatus.PENDING, FundingStatus.UNDER_REVIEW])
            )
        )
    ).scalar_one()

    needs_attention = [
        NeedsAttentionItem(label="funding requests awaiting review", count=pending_count, to="/operations/funding"),
        NeedsAttentionItem(label="KYC reviews pending", count=kyc_pending, to="/operations/kyc"),
        NeedsAttentionItem(
            label="requests needing more information", count=exceptions, to="/operations/funding"
        ),
    ]

    return OperationsOverviewResponse(
        pending_funding=Decimal(pending_funding),
        approved_today=approved_today,
        total_funding_volume=Decimal(total_volume),
        kyc_pending=kyc_pending,
        exceptions=exceptions,
        funding_volume_by_day=volume_by_day,
        funding_by_method=by_method,
        needs_attention=needs_attention,
    )


@router.get("/funding-requests", response_model=list[FundingRequestResponse])
async def list_funding_requests(
    status_filter: FundingStatus | None = Query(None, alias="status"),
    search: str | None = None,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[FundingRequestResponse]:
    query = select(FundingRequest, User).join(User, User.id == FundingRequest.customer_id)
    if status_filter is not None:
        query = query.where(FundingRequest.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                FundingRequest.request_number.ilike(pattern),
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )
    query = query.order_by(FundingRequest.created_at.desc()).limit(200)

    rows = (await db.execute(query)).all()
    return [_request_to_response(fr, customer.full_name) for fr, customer in rows]


async def _load_request_and_customer(db: AsyncSession, funding_request_id: uuid.UUID) -> tuple[FundingRequest, User]:
    row = (
        await db.execute(
            select(FundingRequest, User)
            .join(User, User.id == FundingRequest.customer_id)
            .where(FundingRequest.id == funding_request_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funding request not found.")
    return row[0], row[1]


@router.get("/funding-requests/{funding_request_id}", response_model=FundingDetailResponse)
async def get_funding_request_detail(
    funding_request_id: uuid.UUID,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> FundingDetailResponse:
    fr, customer = await _load_request_and_customer(db, funding_request_id)
    fr = await mark_under_review(db, fr)

    payment_attempt = (
        await db.execute(
            select(PaymentAttempt)
            .where(PaymentAttempt.funding_request_id == fr.id)
            .order_by(PaymentAttempt.created_at.desc())
        )
    ).scalars().first()

    checklist = build_verification_checklist(fr, customer, payment_attempt)
    base = _request_to_response(fr, customer.full_name)

    return FundingDetailResponse(
        **base.model_dump(by_alias=False),
        customer=FundingCustomerSummary(
            full_name=customer.full_name,
            email=customer.email,
            mobile_number=customer.mobile_number,
            kyc_status=customer.kyc_status.value,
            customer_since=customer.created_at,
        ),
        checklist=checklist,
    )


def _check_idempotency_key(idempotency_key: str | None) -> None:
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Idempotency-Key header for this action."
        )


@router.post("/funding-requests/{funding_request_id}/approve", response_model=FundingRequestResponse)
async def approve_request(
    funding_request_id: uuid.UUID,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    _check_idempotency_key(idempotency_key)

    endpoint = f"approve:{funding_request_id}"
    existing_key = (
        await db.execute(select(IdempotencyRecord).where(IdempotencyRecord.key == idempotency_key))
    ).scalar_one_or_none()
    if existing_key is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This approval has already been processed."
        )
    db.add(IdempotencyRecord(key=idempotency_key, endpoint=endpoint))
    await db.commit()

    fr, customer = await _load_request_and_customer(db, funding_request_id)
    fr = await approve_funding_request(db, fr, user)
    return _request_to_response(fr, customer.full_name)


@router.post("/funding-requests/{funding_request_id}/reject", response_model=FundingRequestResponse)
async def reject_request(
    funding_request_id: uuid.UUID,
    body: RejectFundingRequestBody,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    fr, customer = await _load_request_and_customer(db, funding_request_id)
    fr = await reject_funding_request(db, fr, user, body.reason)
    return _request_to_response(fr, customer.full_name)


@router.post("/funding-requests/{funding_request_id}/request-information", response_model=FundingRequestResponse)
async def request_information(
    funding_request_id: uuid.UUID,
    body: RequestInformationBody,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> FundingRequestResponse:
    fr, customer = await _load_request_and_customer(db, funding_request_id)
    fr = await request_additional_information(db, fr, user, body.message)
    return _request_to_response(fr, customer.full_name)


def _kyc_to_response(profile: KycProfile) -> KycProfileResponse:
    personal_info = None
    if profile.date_of_birth and profile.address:
        personal_info = KycPersonalInfoResponse(
            date_of_birth=profile.date_of_birth.isoformat(),
            address=profile.address,
            city=profile.city or "",
            state=profile.state or "",
            pin_code=profile.pin_code or "",
        )

    bank_account = None
    if profile.account_number_masked:
        bank_account = KycBankAccountResponse(
            account_holder_name=profile.account_holder_name or "",
            account_number_masked=profile.account_number_masked,
            ifsc=profile.ifsc or "",
        )

    return KycProfileResponse(
        id=profile.id,
        customer_id=profile.user_id,
        status=profile.status,
        submitted_at=profile.submitted_at,
        personal_info=personal_info,
        documents=[
            KycDocumentResponse(
                id=doc.id,
                type=doc.document_type,
                file_name=doc.file_name,
                status=doc.status,
                uploaded_at=doc.uploaded_at,
            )
            for doc in profile.documents
        ],
        bank_account=bank_account,
        review_notes=profile.review_notes,
    )


@router.get("/kyc-profiles", response_model=list[KycQueueItemResponse])
async def list_kyc_profiles(
    status_filter: KycStatus | None = Query(None, alias="status"),
    search: str | None = None,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[KycQueueItemResponse]:
    query = select(KycProfile, User).join(User, User.id == KycProfile.user_id)
    if status_filter is not None:
        query = query.where(KycProfile.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )
    query = query.order_by(KycProfile.created_at.desc()).limit(200)

    rows = (await db.execute(query)).all()
    results: list[KycQueueItemResponse] = []
    for profile, customer in rows:
        await db.refresh(profile, attribute_names=["documents"])
        results.append(
            KycQueueItemResponse(
                id=profile.id,
                customer_id=profile.user_id,
                customer_name=customer.full_name,
                customer_email=customer.email,
                customer_mobile=customer.mobile_number,
                status=profile.status,
                submitted_at=profile.submitted_at,
                documents_count=len(profile.documents),
            )
        )
    return results


async def _load_profile_and_customer(db: AsyncSession, profile_id: uuid.UUID) -> tuple[KycProfile, User]:
    row = (
        await db.execute(
            select(KycProfile, User).join(User, User.id == KycProfile.user_id).where(KycProfile.id == profile_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KYC profile not found.")
    return row[0], row[1]


@router.get("/kyc-profiles/{profile_id}", response_model=KycReviewDetailResponse)
async def get_kyc_profile_detail(
    profile_id: uuid.UUID,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> KycReviewDetailResponse:
    profile, customer = await _load_profile_and_customer(db, profile_id)
    await db.refresh(profile, attribute_names=["documents"])
    base = _kyc_to_response(profile)

    return KycReviewDetailResponse(
        **base.model_dump(by_alias=False),
        customer=KycCustomerSummary(
            full_name=customer.full_name,
            email=customer.email,
            mobile_number=customer.mobile_number,
            customer_since=customer.created_at,
        ),
    )


@router.post("/kyc-profiles/{profile_id}/approve", response_model=KycProfileResponse)
async def approve_kyc_profile(
    profile_id: uuid.UUID,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> KycProfileResponse:
    _check_idempotency_key(idempotency_key)

    endpoint = f"kyc-approve:{profile_id}"
    existing_key = (
        await db.execute(select(IdempotencyRecord).where(IdempotencyRecord.key == idempotency_key))
    ).scalar_one_or_none()
    if existing_key is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This approval has already been processed."
        )
    db.add(IdempotencyRecord(key=idempotency_key, endpoint=endpoint))
    await db.commit()

    profile, customer = await _load_profile_and_customer(db, profile_id)
    profile = await approve_kyc(db, profile, customer, user)
    await db.refresh(profile, attribute_names=["documents"])
    return _kyc_to_response(profile)


@router.post("/kyc-profiles/{profile_id}/reject", response_model=KycProfileResponse)
async def reject_kyc_profile(
    profile_id: uuid.UUID,
    body: KycRejectBody,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> KycProfileResponse:
    profile, customer = await _load_profile_and_customer(db, profile_id)
    profile = await reject_kyc(db, profile, customer, user, body.reason)
    await db.refresh(profile, attribute_names=["documents"])
    return _kyc_to_response(profile)


@router.post("/kyc-profiles/{profile_id}/request-information", response_model=KycProfileResponse)
async def request_kyc_information(
    profile_id: uuid.UUID,
    body: KycRequestInformationBody,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> KycProfileResponse:
    profile, customer = await _load_profile_and_customer(db, profile_id)
    profile = await request_kyc_additional_information(db, profile, customer, user, body.message)
    await db.refresh(profile, attribute_names=["documents"])
    return _kyc_to_response(profile)


@router.get("/kyc-profiles/{profile_id}/documents/{document_id}")
async def get_kyc_document(
    profile_id: uuid.UUID,
    document_id: uuid.UUID,
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    document = (
        await db.execute(
            select(KycDocument).where(KycDocument.id == document_id, KycDocument.kyc_profile_id == profile_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return FileResponse(document.file_path, filename=document.file_name)

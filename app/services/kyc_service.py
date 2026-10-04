from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import KYC_STATUS_TRANSITIONS, KycDocumentStatus, KycDocumentType, KycStatus
from app.models.kyc import KycDocument, KycProfile
from app.models.user import User
from app.services import audit_service
from app.services.file_storage import save_upload


def _mask_account_number(account_number: str) -> str:
    return f"XXXXXXXX{account_number[-4:]}"


def _ensure_transition(current: KycStatus, target: KycStatus) -> None:
    allowed = KYC_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This KYC profile is already {current.value.replace('_', ' ').lower()} and cannot be changed again.",
        )


async def get_or_create_kyc_profile(db: AsyncSession, customer_id) -> KycProfile:
    profile = (
        await db.execute(select(KycProfile).where(KycProfile.user_id == customer_id))
    ).scalar_one_or_none()
    if profile is not None:
        return profile

    profile = KycProfile(user_id=customer_id, status=KycStatus.NOT_STARTED)
    db.add(profile)
    # Commit (not just flush): callers like GET /kyc/me read-then-return in
    # the same request with no later commit of their own, so without this
    # the newly-created profile is rolled back when the session closes —
    # its id would 404 on every subsequent request, including an Ops
    # reviewer trying to open it from the queue.
    await db.commit()
    await db.refresh(profile)
    return profile


DOCUMENT_FIELD_TYPES: dict[str, KycDocumentType] = {
    "idProof": KycDocumentType.ID_PROOF,
    "addressProof": KycDocumentType.ADDRESS_PROOF,
    "panCard": KycDocumentType.PAN,
}


async def submit_kyc(
    db: AsyncSession,
    *,
    customer: User,
    personal_info: dict[str, Any],
    bank_account: dict[str, Any],
    documents: dict[str, UploadFile],
) -> KycProfile:
    profile = await get_or_create_kyc_profile(db, customer.id)
    _ensure_transition(profile.status, KycStatus.UNDER_REVIEW)

    profile.status = KycStatus.UNDER_REVIEW
    profile.review_notes = None
    profile.submitted_at = datetime.now(UTC)
    dob_raw = personal_info.get("dateOfBirth")
    profile.date_of_birth = datetime.strptime(dob_raw, "%Y-%m-%d").date() if dob_raw else None
    profile.address = personal_info.get("address")
    profile.city = personal_info.get("city")
    profile.state = personal_info.get("state")
    profile.pin_code = personal_info.get("pinCode")

    profile.account_holder_name = bank_account.get("accountHolderName")
    account_number = bank_account.get("accountNumber", "")
    profile.account_number_masked = _mask_account_number(account_number) if account_number else None
    profile.ifsc = bank_account.get("ifsc")

    # Resubmission after REJECTED/ADDITIONAL_INFORMATION_REQUIRED: drop the
    # prior document rows so the profile doesn't accumulate duplicates of
    # the same document type across submissions.
    existing_docs = (
        await db.execute(select(KycDocument).where(KycDocument.kyc_profile_id == profile.id))
    ).scalars().all()
    for doc in existing_docs:
        await db.delete(doc)

    for field_name, upload in documents.items():
        doc_type = DOCUMENT_FIELD_TYPES.get(field_name)
        if doc_type is None or upload is None:
            continue
        file_path = await save_upload(upload, subdir=f"kyc/{customer.id}")
        db.add(
            KycDocument(
                kyc_profile_id=profile.id,
                document_type=doc_type,
                file_name=upload.filename or field_name,
                file_path=file_path,
                status=KycDocumentStatus.SUBMITTED,
                uploaded_at=datetime.now(UTC),
            )
        )

    customer.kyc_status = KycStatus.UNDER_REVIEW

    await db.commit()
    await db.refresh(profile)
    return profile


async def approve_kyc(db: AsyncSession, profile: KycProfile, customer: User, operator: User) -> KycProfile:
    before_status = profile.status
    _ensure_transition(before_status, KycStatus.APPROVED)

    profile.status = KycStatus.APPROVED
    profile.review_notes = None
    profile.reviewed_by = operator.id
    profile.reviewed_at = datetime.now(UTC)
    customer.kyc_status = KycStatus.APPROVED

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="KYC_APPROVED",
        resource_type="KycProfile",
        resource_id=profile.id,
        before_state={"status": before_status.value},
        after_state={"status": KycStatus.APPROVED.value},
    )

    await db.commit()
    await db.refresh(profile)
    return profile


async def reject_kyc(db: AsyncSession, profile: KycProfile, customer: User, operator: User, reason: str) -> KycProfile:
    before_status = profile.status
    _ensure_transition(before_status, KycStatus.REJECTED)

    profile.status = KycStatus.REJECTED
    profile.review_notes = reason
    profile.reviewed_by = operator.id
    profile.reviewed_at = datetime.now(UTC)
    customer.kyc_status = KycStatus.REJECTED

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="KYC_REJECTED",
        resource_type="KycProfile",
        resource_id=profile.id,
        before_state={"status": before_status.value},
        after_state={"status": KycStatus.REJECTED.value},
        reason=reason,
    )

    await db.commit()
    await db.refresh(profile)
    return profile


async def request_kyc_additional_information(
    db: AsyncSession, profile: KycProfile, customer: User, operator: User, message: str
) -> KycProfile:
    before_status = profile.status
    _ensure_transition(before_status, KycStatus.ADDITIONAL_INFORMATION_REQUIRED)

    profile.status = KycStatus.ADDITIONAL_INFORMATION_REQUIRED
    profile.review_notes = message
    profile.reviewed_by = operator.id
    profile.reviewed_at = datetime.now(UTC)
    customer.kyc_status = KycStatus.ADDITIONAL_INFORMATION_REQUIRED

    await audit_service.record_audit_event(
        db,
        actor=operator,
        action="KYC_INFO_REQUESTED",
        resource_type="KycProfile",
        resource_id=profile.id,
        before_state={"status": before_status.value},
        after_state={"status": KycStatus.ADDITIONAL_INFORMATION_REQUIRED.value},
        reason=message,
    )

    await db.commit()
    await db.refresh(profile)
    return profile

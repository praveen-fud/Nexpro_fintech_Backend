from datetime import UTC, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import KycDocumentStatus, KycDocumentType, KycStatus
from app.models.kyc import KycDocument, KycProfile
from app.models.user import User
from app.services.file_storage import save_upload


def _mask_account_number(account_number: str) -> str:
    return f"XXXXXXXX{account_number[-4:]}"


async def get_or_create_kyc_profile(db: AsyncSession, customer_id) -> KycProfile:
    profile = (
        await db.execute(select(KycProfile).where(KycProfile.user_id == customer_id))
    ).scalar_one_or_none()
    if profile is not None:
        return profile

    profile = KycProfile(user_id=customer_id, status=KycStatus.NOT_STARTED)
    db.add(profile)
    await db.flush()
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

    profile.status = KycStatus.UNDER_REVIEW
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

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role
from app.models.kyc import KycProfile
from app.models.user import User
from app.schemas.kyc import (
    KycBankAccountResponse,
    KycDocumentResponse,
    KycPersonalInfoResponse,
    KycProfileResponse,
)
from app.security.deps import require_roles
from app.services.kyc_service import get_or_create_kyc_profile, submit_kyc

router = APIRouter(prefix="/kyc", tags=["kyc"])


def _to_response(profile: KycProfile) -> KycProfileResponse:
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


@router.get("/me", response_model=KycProfileResponse)
async def get_my_kyc(
    user: User = Depends(require_roles(Role.CUSTOMER)), db: AsyncSession = Depends(get_db)
) -> KycProfileResponse:
    profile = await get_or_create_kyc_profile(db, user.id)
    await db.refresh(profile, attribute_names=["documents"])
    return _to_response(profile)


@router.post("/submit", response_model=KycProfileResponse, status_code=status.HTTP_201_CREATED)
async def submit_kyc_endpoint(
    personal_info: str = Form(..., alias="personalInfo"),
    bank_account: str = Form(..., alias="bankAccount"),
    id_proof: UploadFile = File(..., alias="idProof"),
    address_proof: UploadFile = File(..., alias="addressProof"),
    pan_card: UploadFile = File(..., alias="panCard"),
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> KycProfileResponse:
    try:
        personal_info_data = json.loads(personal_info)
        bank_account_data = json.loads(bank_account)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid KYC submission payload.") from exc

    profile = await submit_kyc(
        db,
        customer=user,
        personal_info=personal_info_data,
        bank_account=bank_account_data,
        documents={"idProof": id_proof, "addressProof": address_proof, "panCard": pan_card},
    )
    await db.refresh(profile, attribute_names=["documents"])
    return _to_response(profile)

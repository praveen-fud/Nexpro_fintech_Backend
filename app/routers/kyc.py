import json
import uuid
from typing import TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.models.enums import Role
from app.models.kyc import KycDocument, KycProfile
from app.models.user import User
from app.schemas.kyc import (
    BankAccountInfo,
    KycBankAccountResponse,
    KycDocumentResponse,
    KycPersonalInfoResponse,
    KycProfileResponse,
    PersonalInfo,
)
from app.security.deps import require_roles
from app.services.document_response import document_response
from app.services.kyc_service import get_or_create_kyc_profile, submit_kyc

ModelT = TypeVar("ModelT", bound=BaseModel)

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
            bank_name=profile.bank_name or "",
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


_MAX_JSON_FIELD_CHARS = 4096


def _parse_json_field(raw: str, field_name: str, model: type[ModelT]) -> ModelT:
    """Parse one JSON-encoded multipart field into a validated model.

    These two fields arrive as JSON strings inside a multipart body (the
    upload has to go in the same request), which means FastAPI cannot
    validate them for us from the signature alone. Doing it explicitly here
    is what stops unbounded, unchecked strings from reaching the database —
    previously these were handed straight through as ``dict[str, Any]``.
    """
    if len(raw) > _MAX_JSON_FIELD_CHARS:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", field_name),
                    "msg": "This field is too large.",
                    "type": "value_error",
                }
            ]
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestValidationError(
            [{"loc": ("body", field_name), "msg": "Malformed JSON.", "type": "value_error"}]
        ) from exc
    if not isinstance(parsed, dict):
        raise RequestValidationError(
            [{"loc": ("body", field_name), "msg": "Expected an object.", "type": "value_error"}]
        )

    try:
        return model.model_validate(parsed)
    except ValidationError as exc:
        # Re-raise through FastAPI's own error type so the response keeps the
        # {message, fieldErrors} shape the frontend already understands, with
        # the field path prefixed by which blob it came from.
        raise RequestValidationError(
            [
                {**error, "loc": ("body", field_name, *(str(p) for p in error.get("loc", ())))}
                for error in exc.errors()
            ]
        ) from exc


@router.post(
    "/submit",
    response_model=KycProfileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("kyc_submit", "rate_limit_upload"))],
)
async def submit_kyc_endpoint(
    personal_info: str = Form(..., alias="personalInfo", max_length=_MAX_JSON_FIELD_CHARS),
    bank_account: str = Form(..., alias="bankAccount", max_length=_MAX_JSON_FIELD_CHARS),
    id_proof: UploadFile = File(..., alias="idProof"),
    address_proof: UploadFile = File(..., alias="addressProof"),
    pan_card: UploadFile = File(..., alias="panCard"),
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> KycProfileResponse:
    personal = _parse_json_field(personal_info, "personalInfo", PersonalInfo)
    bank = _parse_json_field(bank_account, "bankAccount", BankAccountInfo)

    profile = await submit_kyc(
        db,
        customer=user,
        personal_info=personal,
        bank_account=bank,
        documents={"idProof": id_proof, "addressProof": address_proof, "panCard": pan_card},
    )
    await db.refresh(profile, attribute_names=["documents"])
    return _to_response(profile)


@router.get("/documents/{document_id}")
async def get_my_kyc_document(
    document_id: uuid.UUID,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    row = (
        await db.execute(
            select(KycDocument, KycProfile)
            .join(KycProfile, KycProfile.id == KycDocument.kyc_profile_id)
            .where(KycDocument.id == document_id)
        )
    ).first()
    if row is None or row[1].user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    document = row[0]
    return document_response(document.file_path, document.file_name)

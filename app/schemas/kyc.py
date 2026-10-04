import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import KycDocumentStatus, KycDocumentType, KycStatus
from app.schemas.common import CamelModel


class PersonalInfo(CamelModel):
    date_of_birth: str
    address: str
    city: str
    state: str
    pin_code: str


class BankAccountInfo(CamelModel):
    account_holder_name: str
    account_number: str
    confirm_account_number: str
    ifsc: str


class KycDocumentResponse(CamelModel):
    id: uuid.UUID
    type: KycDocumentType
    file_name: str
    status: KycDocumentStatus
    uploaded_at: datetime


class KycBankAccountResponse(CamelModel):
    account_holder_name: str
    account_number_masked: str
    ifsc: str


class KycPersonalInfoResponse(CamelModel):
    date_of_birth: str
    address: str
    city: str
    state: str
    pin_code: str


class KycProfileResponse(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    status: KycStatus
    submitted_at: datetime | None
    personal_info: KycPersonalInfoResponse | None
    documents: list[KycDocumentResponse]
    bank_account: KycBankAccountResponse | None
    review_notes: str | None


class KycCustomerSummary(CamelModel):
    full_name: str
    email: str
    mobile_number: str
    customer_since: datetime


class KycQueueItemResponse(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    customer_email: str
    customer_mobile: str
    status: KycStatus
    submitted_at: datetime | None
    documents_count: int


class KycReviewDetailResponse(KycProfileResponse):
    customer: KycCustomerSummary


class KycRejectBody(CamelModel):
    reason: str = Field(min_length=3, max_length=500)


class KycRequestInformationBody(CamelModel):
    message: str = Field(min_length=3, max_length=500)

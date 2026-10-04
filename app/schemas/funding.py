import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.models.enums import FundingMethod, FundingStatus, PaymentStatus
from app.schemas.common import CamelModel


class FundingQuoteResponse(CamelModel):
    requested_amount: Decimal
    fee: Decimal
    wallet_credit: Decimal
    total_payment: Decimal


class CreateFundingRequestBody(CamelModel):
    method: FundingMethod
    amount: Decimal = Field(gt=0)
    payment_details: dict[str, Any] = Field(default_factory=dict)


class FundingRequestResponse(CamelModel):
    id: uuid.UUID
    request_number: str
    customer_id: uuid.UUID
    customer_name: str
    method: FundingMethod
    requested_amount: Decimal
    fee: Decimal
    wallet_credit: Decimal
    status: FundingStatus
    payment_status: PaymentStatus
    reference: str
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime


class BeneficiaryInfoResponse(CamelModel):
    account_name: str
    account_number_masked: str
    ifsc: str
    reference: str


class ChecklistItem(CamelModel):
    key: str
    label: str
    result: str  # pass | fail | warning
    detail: str | None = None


class FundingCustomerSummary(CamelModel):
    full_name: str
    email: str
    mobile_number: str
    kyc_status: str
    customer_since: datetime


class FundingDetailResponse(FundingRequestResponse):
    customer: FundingCustomerSummary
    checklist: list[ChecklistItem]


class RejectFundingRequestBody(CamelModel):
    reason: str = Field(min_length=3, max_length=500)


class RequestInformationBody(CamelModel):
    message: str = Field(min_length=3, max_length=500)

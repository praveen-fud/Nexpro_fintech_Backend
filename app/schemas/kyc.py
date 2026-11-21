import re
import unicodedata
import uuid
from datetime import UTC, date, datetime

from pydantic import Field, field_validator, model_validator

from app.models.enums import KycDocumentStatus, KycDocumentType, KycStatus
from app.schemas.common import CamelModel

# Every max_length below is <= the matching column width in models/kyc.py.
# Without that, an over-long value reaches Postgres and comes back as a 500
# (StringDataRightTruncation) instead of a field-level 422.
_IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_ACCOUNT_NUMBER_PATTERN = re.compile(r"^\d{9,18}$")
_PIN_PATTERN = re.compile(r"^\d{6}$")
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'\-]*$")
# Bank names legitimately contain digits, ampersands and dots
# ("HDFC Bank Ltd.", "State Bank of India", "DBS Bank India").
_BANK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()\-]*$")

_MIN_AGE_YEARS = 18
_MAX_AGE_YEARS = 120


def _strip_control_chars(value: str) -> str:
    """Remove control characters and collapse runs of whitespace.

    Keeps newlines and ANSI escapes out of values that later land in Ops
    review screens, audit records and exported reports.
    """
    without_controls = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    return " ".join(without_controls.split())


class PersonalInfo(CamelModel):
    """Validated shape of the ``personalInfo`` form field on /kyc/submit."""

    date_of_birth: date
    address: str = Field(min_length=5, max_length=255)
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    pin_code: str = Field(min_length=6, max_length=6)

    @field_validator("address", "city", "state", mode="before")
    @classmethod
    def clean_text(cls, v: object) -> object:
        return _strip_control_chars(v) if isinstance(v, str) else v

    @field_validator("pin_code")
    @classmethod
    def valid_pin(cls, v: str) -> str:
        v = v.strip()
        if not _PIN_PATTERN.match(v):
            raise ValueError("Enter a valid 6-digit PIN code")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def plausible_dob(cls, v: date) -> date:
        # UTC rather than the host's local date: the server's timezone should
        # not decide whether someone turns 18 today.
        today = datetime.now(UTC).date()
        if v > today:
            raise ValueError("Date of birth cannot be in the future")
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < _MIN_AGE_YEARS:
            raise ValueError(f"You must be at least {_MIN_AGE_YEARS} to open an account")
        if age > _MAX_AGE_YEARS:
            raise ValueError("Enter a valid date of birth")
        return v


class BankAccountInfo(CamelModel):
    """Validated shape of the ``bankAccount`` form field on /kyc/submit.

    The raw account number is validated here and used only to derive the
    masked form that gets stored — it is never persisted in full.
    """

    account_holder_name: str = Field(min_length=2, max_length=120)
    account_number: str = Field(min_length=9, max_length=18)
    confirm_account_number: str = Field(min_length=9, max_length=18)
    bank_name: str = Field(min_length=2, max_length=100)
    ifsc: str = Field(min_length=11, max_length=11)

    @field_validator("account_holder_name", "bank_name", mode="before")
    @classmethod
    def clean_free_text(cls, v: object) -> object:
        return _strip_control_chars(v) if isinstance(v, str) else v

    @field_validator("account_holder_name")
    @classmethod
    def plausible_holder_name(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError("Use letters, spaces, apostrophes and hyphens only")
        return v

    @field_validator("bank_name")
    @classmethod
    def plausible_bank_name(cls, v: str) -> str:
        if not _BANK_NAME_PATTERN.match(v):
            raise ValueError("Enter a valid bank name")
        return v

    @field_validator("account_number", "confirm_account_number")
    @classmethod
    def numeric_account(cls, v: str) -> str:
        v = v.strip()
        if not _ACCOUNT_NUMBER_PATTERN.match(v):
            raise ValueError("Enter a valid account number (9-18 digits)")
        return v

    @field_validator("ifsc")
    @classmethod
    def valid_ifsc(cls, v: str) -> str:
        v = v.strip().upper()
        if not _IFSC_PATTERN.match(v):
            raise ValueError("Enter a valid IFSC code, e.g. HDFC0001234")
        return v

    @model_validator(mode="after")
    def accounts_match(self) -> "BankAccountInfo":
        # The frontend checks this too; the server has to as well, or a
        # direct API call can store a masked tail that does not match the
        # account the customer meant to use.
        if self.account_number != self.confirm_account_number:
            raise ValueError("Account numbers do not match")
        return self


class KycDocumentResponse(CamelModel):
    id: uuid.UUID
    type: KycDocumentType
    file_name: str
    status: KycDocumentStatus
    uploaded_at: datetime


class KycBankAccountResponse(CamelModel):
    account_holder_name: str
    account_number_masked: str
    bank_name: str = ""
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

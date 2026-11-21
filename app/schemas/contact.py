import re
import unicodedata
import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.models.enums import ContactEnquiryStatus, EnquirerType
from app.schemas.common import CamelModel

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'\-]*$")


def _clean(value: str) -> str:
    """Strip control characters and collapse whitespace.

    This arrives from an unauthenticated form and is later read by staff in
    the back office and in notification emails, so newlines and escape
    sequences have no business surviving the trip.
    """
    without_controls = "".join(c for c in value if unicodedata.category(c)[0] != "C" or c == "\n")
    return "\n".join(" ".join(line.split()) for line in without_controls.splitlines()).strip()


class ContactEnquiryRequest(CamelModel):
    enquirer_type: EnquirerType
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr = Field(max_length=254)
    mobile_number: str = Field(pattern=r"^[6-9]\d{9}$")
    organisation: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=20, max_length=2000)

    @field_validator("full_name", "organisation", "message", mode="before")
    @classmethod
    def clean_text(cls, v: object) -> object:
        return _clean(v) if isinstance(v, str) else v

    @field_validator("full_name")
    @classmethod
    def plausible_name(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError("Use letters, spaces, apostrophes and hyphens only")
        return v

    @field_validator("organisation")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return v or None


class ContactEnquiryResponse(CamelModel):
    """What the public form gets back — an acknowledgement, nothing more.

    Deliberately does not echo the submitted content: there is no reason for
    an unauthenticated endpoint to reflect input back to its caller.
    """

    id: uuid.UUID
    status: ContactEnquiryStatus
    created_at: datetime


class ContactEnquiryDetail(ContactEnquiryResponse):
    """The back-office view, for Operations and Super Admin only."""

    enquirer_type: EnquirerType
    full_name: str
    email: str
    mobile_number: str
    organisation: str | None
    message: str

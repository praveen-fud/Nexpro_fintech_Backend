from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ContactEnquiryStatus, EnquirerType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ContactEnquiry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An enquiry submitted from the public contact form.

    Unauthenticated writes, so everything here is bounded at the schema layer
    and the endpoint is rate limited. Nothing in this table is ever rendered
    as HTML — it is read by staff in the back office as plain text.
    """

    __tablename__ = "contact_enquiries"

    enquirer_type: Mapped[EnquirerType] = mapped_column(Enum(EnquirerType, native_enum=False))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), index=True)
    mobile_number: Mapped[str] = mapped_column(String(10))
    organisation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message: Mapped[str] = mapped_column(String(2000))
    status: Mapped[ContactEnquiryStatus] = mapped_column(
        Enum(ContactEnquiryStatus, native_enum=False), default=ContactEnquiryStatus.NEW
    )

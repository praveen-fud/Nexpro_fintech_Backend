import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import KycDocumentStatus, KycDocumentType, KycStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class KycProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "kyc_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), unique=True)
    status: Mapped[KycStatus] = mapped_column(Enum(KycStatus, native_enum=False), default=KycStatus.NOT_STARTED)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pin_code: Mapped[str | None] = mapped_column(String(6), nullable=True)

    account_holder_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number_masked: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(11), nullable=True)

    review_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="kyc_profile", foreign_keys=[user_id])
    documents: Mapped[list["KycDocument"]] = relationship(back_populates="kyc_profile", cascade="all, delete-orphan")


class KycDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "kyc_documents"

    kyc_profile_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("kyc_profiles.id"))
    document_type: Mapped[KycDocumentType] = mapped_column(Enum(KycDocumentType, native_enum=False))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[KycDocumentStatus] = mapped_column(
        Enum(KycDocumentStatus, native_enum=False), default=KycDocumentStatus.SUBMITTED
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    kyc_profile: Mapped["KycProfile"] = relationship(back_populates="documents")

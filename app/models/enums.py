import enum


class Role(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    OPERATIONS = "OPERATIONS"
    SUPER_ADMIN = "SUPER_ADMIN"


class KycStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ADDITIONAL_INFORMATION_REQUIRED = "ADDITIONAL_INFORMATION_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class KycDocumentType(str, enum.Enum):
    ID_PROOF = "ID_PROOF"
    ADDRESS_PROOF = "ADDRESS_PROOF"
    PAN = "PAN"
    PHOTO = "PHOTO"
    BANK_PROOF = "BANK_PROOF"


class KycDocumentStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# Valid KYC-profile state transitions. Enforced centrally in
# services/kyc_service.py — never mutate `status` directly elsewhere.
# Submission is modeled as "current status -> UNDER_REVIEW", which is why
# UNDER_REVIEW isn't in its own allowed-target set (blocks re-submitting
# while already under review) and APPROVED's set is empty (blocks
# re-submitting after approval).
KYC_STATUS_TRANSITIONS: dict[KycStatus, set[KycStatus]] = {
    KycStatus.NOT_STARTED: {KycStatus.UNDER_REVIEW},
    KycStatus.SUBMITTED: {KycStatus.UNDER_REVIEW},
    KycStatus.UNDER_REVIEW: {KycStatus.APPROVED, KycStatus.REJECTED, KycStatus.ADDITIONAL_INFORMATION_REQUIRED},
    KycStatus.ADDITIONAL_INFORMATION_REQUIRED: {KycStatus.UNDER_REVIEW},
    KycStatus.APPROVED: set(),
    KycStatus.REJECTED: {KycStatus.UNDER_REVIEW},
}


class FundingMethod(str, enum.Enum):
    CREDIT_CARD = "CREDIT_CARD"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"


class FundingStatus(str, enum.Enum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    ADDITIONAL_INFORMATION_REQUIRED = "ADDITIONAL_INFORMATION_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


# Valid funding-request state transitions. Enforced centrally in
# services/funding_service.py — never mutate `status` directly elsewhere.
FUNDING_STATUS_TRANSITIONS: dict[FundingStatus, set[FundingStatus]] = {
    FundingStatus.PENDING: {FundingStatus.UNDER_REVIEW, FundingStatus.CANCELLED, FundingStatus.FAILED},
    FundingStatus.UNDER_REVIEW: {
        FundingStatus.APPROVED,
        FundingStatus.REJECTED,
        FundingStatus.ADDITIONAL_INFORMATION_REQUIRED,
    },
    FundingStatus.ADDITIONAL_INFORMATION_REQUIRED: {FundingStatus.UNDER_REVIEW},
    FundingStatus.APPROVED: set(),
    FundingStatus.REJECTED: set(),
    FundingStatus.CANCELLED: set(),
    FundingStatus.FAILED: set(),
}


class PaymentStatus(str, enum.Enum):
    INITIATED = "INITIATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    REVERSED = "REVERSED"


class TransactionType(str, enum.Enum):
    FUNDING = "FUNDING"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"


class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class LedgerDirection(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class LedgerStatus(str, enum.Enum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class SupportTicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"

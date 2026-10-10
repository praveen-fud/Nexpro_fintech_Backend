"""
Import every model module here so Base.metadata is fully populated (for
Alembic autogenerate and dev create_all) and so SQLAlchemy's declarative
registry can resolve string-based relationship() forward references
across modules without circular imports.
"""

from app.models.audit import AuditLog
from app.models.config import FeeRule, PlatformLimit
from app.models.funding import FundingRequest, IdempotencyRecord, PaymentAttempt
from app.models.kyc import KycDocument, KycProfile
from app.models.support import SupportTicket
from app.models.transaction import Transaction
from app.models.user import AuthSession, RefreshToken, User
from app.models.wallet import Wallet, WalletLedgerEntry

__all__ = [
    "AuditLog",
    "FeeRule",
    "PlatformLimit",
    "FundingRequest",
    "IdempotencyRecord",
    "PaymentAttempt",
    "KycDocument",
    "KycProfile",
    "SupportTicket",
    "Transaction",
    "AuthSession",
    "RefreshToken",
    "User",
    "Wallet",
    "WalletLedgerEntry",
]

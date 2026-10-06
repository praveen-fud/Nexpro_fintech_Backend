"""
One-time cleanup script — removes all demo/test data from the database.

Preserves:
  - SUPER_ADMIN and OPERATIONS user accounts
  - Fee rules and platform limits (seeded by bootstrap)

Deletes:
  - All CUSTOMER accounts and their associated data
  - All wallets, ledger entries, transactions
  - All funding requests, payment attempts, idempotency records
  - All KYC profiles and documents
  - All audit logs
  - All refresh tokens for deleted users

Run once via Railway shell:
    python -m app.cleanup_demo_data
"""

import asyncio

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.audit import AuditLog
from app.models.enums import Role
from app.models.funding import FundingRequest, IdempotencyRecord, PaymentAttempt
from app.models.kyc import KycDocument, KycProfile
from app.models.transaction import Transaction
from app.models.user import RefreshToken, User
from app.models.wallet import Wallet, WalletLedgerEntry


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        # Collect customer IDs before deleting
        customer_ids = (
            await db.execute(select(User.id).where(User.role == Role.CUSTOMER))
        ).scalars().all()

        if not customer_ids:
            print("No customer accounts found — database is already clean.")
            return

        print(f"Found {len(customer_ids)} customer account(s) to remove.")

        # Delete in FK-safe order
        await db.execute(delete(AuditLog))
        print("  ✓ Audit logs cleared")

        await db.execute(delete(KycDocument))
        await db.execute(delete(KycProfile))
        print("  ✓ KYC profiles and documents cleared")

        await db.execute(delete(IdempotencyRecord))
        await db.execute(delete(PaymentAttempt))
        await db.execute(delete(FundingRequest))
        print("  ✓ Funding requests and payment attempts cleared")

        await db.execute(delete(WalletLedgerEntry))
        await db.execute(delete(Transaction))
        await db.execute(delete(Wallet))
        print("  ✓ Wallets, ledger entries, and transactions cleared")

        await db.execute(delete(RefreshToken))
        await db.execute(delete(User).where(User.role == Role.CUSTOMER))
        print("  ✓ Customer accounts and refresh tokens cleared")

        await db.commit()

    print("\nDone. All demo data removed.")
    print("Fee rules, platform limits, and staff accounts are untouched.")


if __name__ == "__main__":
    asyncio.run(cleanup())

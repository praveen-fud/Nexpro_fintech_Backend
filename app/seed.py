"""
Development seed script. Populates demo users, fee rules, platform
limits, and a few sample funding requests/transactions so the UI has
realistic data to render immediately.

Run with: python -m app.seed
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.models.config import FeeRule, PlatformLimit
from app.models.enums import (
    FundingMethod,
    FundingStatus,
    KycStatus,
    LedgerDirection,
    LedgerStatus,
    PaymentStatus,
    Role,
    TransactionStatus,
    TransactionType,
)
from app.models.funding import FundingRequest, PaymentAttempt
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet, WalletLedgerEntry
from app.security.passwords import hash_password

DEMO_PASSWORD = "Password123"


async def seed_fee_rules_and_limits(session) -> None:
    existing = (await session.execute(select(FeeRule))).scalars().first()
    if existing:
        return

    session.add_all(
        [
            FeeRule(method=FundingMethod.CREDIT_CARD, fee_type="PERCENTAGE", percentage=Decimal("2.00"), min_fee=Decimal("99"), max_fee=Decimal("2000"), is_enabled=True),
            FeeRule(method=FundingMethod.UPI, fee_type="FIXED", fixed_amount=Decimal("0"), is_enabled=True),
            FeeRule(method=FundingMethod.BANK_TRANSFER, fee_type="FIXED", fixed_amount=Decimal("0"), is_enabled=True),
        ]
    )
    session.add(
        PlatformLimit(scope="GLOBAL", per_transaction=Decimal("200000"), daily=Decimal("500000"), monthly=Decimal("2000000"))
    )


async def _create_user(session, *, full_name, email, mobile, role, kyc_status=KycStatus.NOT_STARTED) -> User:
    user = User(
        full_name=full_name,
        email=email,
        mobile_number=mobile,
        password_hash=hash_password(DEMO_PASSWORD),
        role=role,
        kyc_status=kyc_status,
        is_active=True,
        email_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _create_wallet_with_ledger(session, user: User, *, available: Decimal, pending: Decimal) -> Wallet:
    wallet = Wallet(user_id=user.id, wallet_number=f"NXP-{user.id.hex[:8].upper()}")
    session.add(wallet)
    await session.flush()

    if available > 0:
        txn = Transaction(
            transaction_number=f"TXN-SEED-{user.id.hex[:6]}A",
            customer_id=user.id,
            type=TransactionType.FUNDING,
            amount=available,
            fee=Decimal("0"),
            status=TransactionStatus.COMPLETED,
            method=FundingMethod.BANK_TRANSFER,
            reference=f"NXP-{user.mobile_number[-5:]}-SEED1",
            description="Wallet Funding via Bank Transfer",
        )
        session.add(txn)
        await session.flush()
        session.add(
            WalletLedgerEntry(
                wallet_id=wallet.id,
                transaction_id=txn.id,
                entry_type="FUNDING_CREDIT",
                direction=LedgerDirection.CREDIT,
                amount=available,
                status=LedgerStatus.POSTED,
                reference=txn.reference,
                created_at=datetime.now(UTC) - timedelta(days=5),
            )
        )

    if pending > 0:
        pending_request = FundingRequest(
            request_number=f"FR-SEED-{user.id.hex[:6]}P",
            customer_id=user.id,
            method=FundingMethod.UPI,
            requested_amount=pending,
            fee=Decimal("0"),
            wallet_credit=pending,
            status=FundingStatus.UNDER_REVIEW,
            payment_status=PaymentStatus.PENDING,
            reference=f"NXP-{user.mobile_number[-5:]}-SEED2",
        )
        session.add(pending_request)
        await session.flush()

        pending_txn = Transaction(
            transaction_number=f"TXN-SEED-{user.id.hex[:6]}B",
            customer_id=user.id,
            funding_request_id=pending_request.id,
            type=TransactionType.FUNDING,
            amount=pending,
            fee=Decimal("0"),
            status=TransactionStatus.PENDING,
            method=FundingMethod.UPI,
            reference=pending_request.reference,
            description="Wallet Funding via UPI",
        )
        session.add(pending_txn)
        await session.flush()

        session.add(
            PaymentAttempt(
                funding_request_id=pending_request.id,
                provider="MOCK",
                status=PaymentStatus.PENDING,
                masked_reference="seed.customer@upi",
                details={"upiId": "seed.customer@upi"},
            )
        )
        session.add(
            WalletLedgerEntry(
                wallet_id=wallet.id,
                transaction_id=pending_txn.id,
                entry_type="FUNDING_CREDIT",
                direction=LedgerDirection.CREDIT,
                amount=pending,
                status=LedgerStatus.PENDING,
                reference=pending_request.reference,
                created_at=datetime.now(UTC) - timedelta(hours=6),
            )
        )

    return wallet


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing_customer = (
            await session.execute(select(User).where(User.email == "customer@nexpro.test"))
        ).scalar_one_or_none()
        if existing_customer is not None:
            print("Seed data already present — skipping.")
            return

        await seed_fee_rules_and_limits(session)

        customer = await _create_user(
            session,
            full_name="Rahul Sharma",
            email="customer@nexpro.test",
            mobile="9876543210",
            role=Role.CUSTOMER,
            kyc_status=KycStatus.APPROVED,
        )
        await _create_wallet_with_ledger(session, customer, available=Decimal("75000"), pending=Decimal("25000"))

        extra_customers = [
            ("Ananya Singh", "ananya.singh@example.test", "9876500001", KycStatus.UNDER_REVIEW),
            ("Arjun Mehta", "arjun.mehta@example.test", "9876500002", KycStatus.NOT_STARTED),
            ("Neha Patel", "neha.patel@example.test", "9876500003", KycStatus.APPROVED),
        ]
        for full_name, email, mobile, kyc_status in extra_customers:
            extra_user = await _create_user(
                session, full_name=full_name, email=email, mobile=mobile, role=Role.CUSTOMER, kyc_status=kyc_status
            )
            await _create_wallet_with_ledger(session, extra_user, available=Decimal("10000"), pending=Decimal("0"))

        await _create_user(
            session,
            full_name="Priya Nair",
            email="ops@nexpro.test",
            mobile="9999900001",
            role=Role.OPERATIONS,
        )
        await _create_user(
            session,
            full_name="Nexpro Admin",
            email="admin@nexpro.test",
            mobile="9999900002",
            role=Role.SUPER_ADMIN,
        )

        await session.commit()
        print("Seed data created.")
        print("  customer@nexpro.test / Password123")
        print("  ops@nexpro.test / Password123")
        print("  admin@nexpro.test / Password123")


if __name__ == "__main__":
    asyncio.run(seed())

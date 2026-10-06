"""
Configuration bootstrap — seeds fee rules and platform limits only.

No demo users are created here.  Customer accounts are self-registered.
Operations and Super Admin accounts are provisioned via:

    python -m app.create_admin --email admin@yourcompany.com \\
        --full-name "Admin Name" --mobile 9XXXXXXXXX --role SUPER_ADMIN

Run: python -m app.seed
"""

import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.models.config import FeeRule, PlatformLimit
from app.models.enums import FundingMethod


async def seed_config(session) -> bool:
    """Seed fee rules and platform limits if they don't exist yet.
    Returns True if anything was written."""
    existing = (await session.execute(select(FeeRule))).scalars().first()
    if existing:
        return False

    session.add_all([
        FeeRule(
            method=FundingMethod.CREDIT_CARD,
            fee_type="PERCENTAGE",
            percentage=Decimal("2.00"),
            min_fee=Decimal("99"),
            max_fee=Decimal("2000"),
            is_enabled=True,
        ),
        FeeRule(
            method=FundingMethod.UPI,
            fee_type="FIXED",
            fixed_amount=Decimal("0"),
            is_enabled=True,
        ),
        FeeRule(
            method=FundingMethod.BANK_TRANSFER,
            fee_type="FIXED",
            fixed_amount=Decimal("0"),
            is_enabled=True,
        ),
    ])
    session.add(
        PlatformLimit(
            scope="GLOBAL",
            per_transaction=Decimal("200000"),
            daily=Decimal("500000"),
            monthly=Decimal("2000000"),
        )
    )
    return True


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        written = await seed_config(session)
        if written:
            await session.commit()
            print("Platform configuration seeded (fee rules + limits).")
        else:
            print("Configuration already present — nothing to do.")

    print()
    print("To create the first Super Admin account run:")
    print("  python -m app.create_admin --email admin@yourcompany.com \\")
    print('      --full-name "Your Name" --mobile 9XXXXXXXXX --role SUPER_ADMIN')


if __name__ == "__main__":
    asyncio.run(seed())

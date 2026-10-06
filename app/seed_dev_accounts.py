"""
Creates three local dev accounts — one per role — with the same password.
Safe to run multiple times: skips any account whose email already exists.

    python -m app.seed_dev_accounts

Accounts created:
  superadmin@nexpro.dev   SUPER_ADMIN
  ops@nexpro.dev          OPERATIONS
  customer@nexpro.dev     CUSTOMER      (+ wallet provisioned)

Password for all three: Nexpro@123

SAFETY: This script aborts immediately unless ENVIRONMENT=development.
It must never run against a staging or production database.
"""

import asyncio
import os
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import KycStatus, Role
from app.models.user import User
from app.models.wallet import Wallet
from app.security.passwords import hash_password

# ── Environment guard ──────────────────────────────────────────────────────────
# Abort if this isn't explicitly a development environment.
# This prevents accidental execution on staging or production servers.
_env = os.getenv("ENVIRONMENT", "").lower()
if _env != "development":
    print(
        f"[seed_dev_accounts] BLOCKED — ENVIRONMENT={_env!r}.\n"
        "This script only runs when ENVIRONMENT=development.\n"
        "It must never run against a staging or production database."
    )
    sys.exit(1)
# ──────────────────────────────────────────────────────────────────────────────

PASSWORD = "Nexpro@123"

ACCOUNTS = [
    {
        "full_name": "Super Admin",
        "email": "superadmin@nexpro.dev",
        "mobile_number": "9000000001",
        "role": Role.SUPER_ADMIN,
        "kyc_status": KycStatus.APPROVED,
        "wallet": False,
    },
    {
        "full_name": "Operations Staff",
        "email": "ops@nexpro.dev",
        "mobile_number": "9000000002",
        "role": Role.OPERATIONS,
        "kyc_status": KycStatus.APPROVED,
        "wallet": False,
    },
    {
        "full_name": "Demo Customer",
        "email": "customer@nexpro.dev",
        "mobile_number": "9000000003",
        "role": Role.CUSTOMER,
        "kyc_status": KycStatus.APPROVED,
        "wallet": True,
    },
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        for acc in ACCOUNTS:
            existing = (
                await db.execute(select(User).where(User.email == acc["email"]))
            ).scalar_one_or_none()

            if existing:
                print(f"  skip  {acc['email']} — already exists (id={existing.id})")
                continue

            user = User(
                full_name=acc["full_name"],
                email=acc["email"],
                mobile_number=acc["mobile_number"],
                password_hash=hash_password(PASSWORD),
                role=acc["role"],
                kyc_status=acc["kyc_status"],
                is_active=True,
                email_verified=True,
            )
            db.add(user)
            await db.flush()  # get user.id before creating wallet

            if acc["wallet"]:
                wallet_number = f"NXW{str(user.id).replace('-', '')[:12].upper()}"
                wallet = Wallet(
                    user_id=user.id,
                    wallet_number=wallet_number,
                    currency="INR",
                )
                db.add(wallet)
                print(f"  created {acc['role'].value:12}  {acc['email']}  (wallet: {wallet_number})")
            else:
                print(f"  created {acc['role'].value:12}  {acc['email']}")

        await db.commit()

    print()
    print("Done. Login with any of the accounts above.")
    print(f"Password: {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())

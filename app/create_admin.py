"""
Creates a single OPERATIONS or SUPER_ADMIN account. There is deliberately
no public sign-up for these roles (anyone could register as internal staff
otherwise) — this is the one way to provision the first privileged account
in an environment. Run it manually, once per account needed, via whoever
has shell access to the deployment (e.g. `railway run` or Railway's web
shell for the backend service). It is never invoked automatically by the
Docker image or any HTTP route.

Usage:
    python -m app.create_admin --email ops@yourcompany.com --full-name "Jane Doe" \\
        --mobile 9876543210 --role OPERATIONS

If --password is omitted you'll be prompted for one (hidden input) instead
of it landing in shell history.
"""

import argparse
import asyncio
import getpass
import re
import sys

from sqlalchemy import or_, select

from app.core.database import AsyncSessionLocal
from app.models.enums import Role
from app.models.user import User
from app.security.passwords import hash_password

MOBILE_PATTERN = re.compile(r"^[6-9]\d{9}$")


async def create_admin(*, email: str, full_name: str, mobile: str, role: Role, password: str) -> None:
    if not MOBILE_PATTERN.match(mobile):
        print(f"Invalid mobile number: {mobile!r} (expected a 10-digit number starting 6-9)", file=sys.stderr)
        raise SystemExit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(1)

    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(User).where(or_(User.email == email, User.mobile_number == mobile)))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"An account with this email or mobile already exists (id={existing.id}).", file=sys.stderr)
            raise SystemExit(1)

        user = User(
            full_name=full_name,
            email=email,
            mobile_number=mobile,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    print(f"Created {role.value} account: {email} (id={user.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an OPERATIONS or SUPER_ADMIN account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--mobile", required=True, help="10-digit mobile number, e.g. 9876543210")
    parser.add_argument("--role", required=True, choices=["OPERATIONS", "SUPER_ADMIN"])
    parser.add_argument("--password", help="Omit to be prompted (recommended — avoids shell history).")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password (min 8 characters): ")

    asyncio.run(
        create_admin(
            email=args.email,
            full_name=args.full_name,
            mobile=args.mobile,
            role=Role(args.role),
            password=password,
        )
    )


if __name__ == "__main__":
    main()

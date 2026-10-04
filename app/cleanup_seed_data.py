"""
Removes the known dev/demo seed accounts (and everything that references
them) from a database — for scrubbing a production database that was
accidentally seeded by an earlier version of the Docker image, which ran
`app.seed` (fake accounts, including a Super Admin with a publicly-known
password) on every deploy. That's no longer wired up (see Dockerfile /
app.bootstrap), but doesn't retroactively clean a database it already ran
against.

Only ever touches rows belonging to this exact, hardcoded list of seed
emails — never a wildcard. No FK in this schema cascades on delete (see
alembic/versions/68ffd58b9389_initial_schema.py), so this runs in a fixed
dependency order (children before parents) inside one transaction: nothing
commits unless every step succeeds.

Usage (defaults to a dry run — reports what it would delete, changes
nothing):
    python -m app.cleanup_seed_data

Actually delete:
    python -m app.cleanup_seed_data --confirm
"""

import argparse
import asyncio

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal

SEED_EMAILS = [
    "customer@nexpro.test",
    "ops@nexpro.test",
    "admin@nexpro.test",
    "ananya.singh@example.test",
    "arjun.mehta@example.test",
    "neha.patel@example.test",
]

# (step description, SELECT to count affected rows, UPDATE/DELETE to apply)
# Steps 1-3 detach references FROM ROWS NOT BEING DELETED (e.g. a real
# customer's request reviewed by a seed Ops account) — defensive, in case
# this ever runs against a DB that also has real data. Steps 4-13 delete
# rows actually owned by the seed users, children before parents.
STEPS = [
    (
        "detach funding_requests.assigned_to",
        "SELECT count(*) FROM funding_requests WHERE assigned_to IN (SELECT id FROM users WHERE email IN :emails)",
        "UPDATE funding_requests SET assigned_to = NULL WHERE assigned_to IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "detach funding_requests.reviewed_by",
        "SELECT count(*) FROM funding_requests WHERE reviewed_by IN (SELECT id FROM users WHERE email IN :emails)",
        "UPDATE funding_requests SET reviewed_by = NULL WHERE reviewed_by IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "detach kyc_profiles.reviewed_by",
        "SELECT count(*) FROM kyc_profiles WHERE reviewed_by IN (SELECT id FROM users WHERE email IN :emails)",
        "UPDATE kyc_profiles SET reviewed_by = NULL WHERE reviewed_by IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete audit_logs (actor = seed user)",
        "SELECT count(*) FROM audit_logs WHERE actor_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM audit_logs WHERE actor_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete wallet_ledger_entries (by wallet)",
        "SELECT count(*) FROM wallet_ledger_entries WHERE wallet_id IN (SELECT id FROM wallets WHERE user_id IN (SELECT id FROM users WHERE email IN :emails))",
        "DELETE FROM wallet_ledger_entries WHERE wallet_id IN (SELECT id FROM wallets WHERE user_id IN (SELECT id FROM users WHERE email IN :emails))",
    ),
    (
        "delete wallet_ledger_entries (by transaction)",
        "SELECT count(*) FROM wallet_ledger_entries WHERE transaction_id IN (SELECT id FROM transactions WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails))",
        "DELETE FROM wallet_ledger_entries WHERE transaction_id IN (SELECT id FROM transactions WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails))",
    ),
    (
        "delete payment_attempts",
        "SELECT count(*) FROM payment_attempts WHERE funding_request_id IN (SELECT id FROM funding_requests WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails))",
        "DELETE FROM payment_attempts WHERE funding_request_id IN (SELECT id FROM funding_requests WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails))",
    ),
    (
        "delete kyc_documents",
        "SELECT count(*) FROM kyc_documents WHERE kyc_profile_id IN (SELECT id FROM kyc_profiles WHERE user_id IN (SELECT id FROM users WHERE email IN :emails))",
        "DELETE FROM kyc_documents WHERE kyc_profile_id IN (SELECT id FROM kyc_profiles WHERE user_id IN (SELECT id FROM users WHERE email IN :emails))",
    ),
    (
        "delete transactions",
        "SELECT count(*) FROM transactions WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM transactions WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete funding_requests",
        "SELECT count(*) FROM funding_requests WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM funding_requests WHERE customer_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete kyc_profiles",
        "SELECT count(*) FROM kyc_profiles WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM kyc_profiles WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete wallets",
        "SELECT count(*) FROM wallets WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM wallets WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete refresh_tokens",
        "SELECT count(*) FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
        "DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE email IN :emails)",
    ),
    (
        "delete users",
        "SELECT count(*) FROM users WHERE email IN :emails",
        "DELETE FROM users WHERE email IN :emails",
    ),
]


def _stmt(sql: str):
    return text(sql).bindparams(bindparam("emails", expanding=True))


async def cleanup(*, confirm: bool) -> None:
    params = {"emails": SEED_EMAILS}
    async with AsyncSessionLocal() as session:
        found = (
            await session.execute(_stmt("SELECT email, role FROM users WHERE email IN :emails"), params)
        ).all()
        if not found:
            print("No seed accounts found — nothing to do.")
            return

        print(f"Found {len(found)} seed account(s):")
        for email, role in found:
            print(f"  {email}  ({role})")
        print()

        for description, select_sql, action_sql in STEPS:
            count = (await session.execute(_stmt(select_sql), params)).scalar_one()
            label = "Will" if confirm else "Would (dry run)"
            print(f"  {label} {description}: {count} row(s)")
            if confirm and count:
                await session.execute(_stmt(action_sql), params)

        if confirm:
            await session.commit()
            print("\nDone — committed.")
        else:
            await session.rollback()
            print("\nDry run only — nothing was changed. Re-run with --confirm to actually delete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Actually delete. Omit for a dry run.")
    args = parser.parse_args()
    asyncio.run(cleanup(confirm=args.confirm))


if __name__ == "__main__":
    main()

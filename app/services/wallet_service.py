import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LedgerDirection, LedgerStatus
from app.models.wallet import Wallet, WalletLedgerEntry


async def get_or_create_wallet(db: AsyncSession, user_id: uuid.UUID) -> Wallet:
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet is not None:
        return wallet

    wallet = Wallet(user_id=user_id, wallet_number=f"NXP-{uuid.uuid4().hex[:8].upper()}")
    db.add(wallet)
    await db.flush()
    return wallet


async def _sum_entries(db: AsyncSession, wallet_id: uuid.UUID, status: LedgerStatus) -> Decimal:
    credit_sum = (
        await db.execute(
            select(func.coalesce(func.sum(WalletLedgerEntry.amount), 0)).where(
                WalletLedgerEntry.wallet_id == wallet_id,
                WalletLedgerEntry.status == status,
                WalletLedgerEntry.direction == LedgerDirection.CREDIT,
            )
        )
    ).scalar_one()
    debit_sum = (
        await db.execute(
            select(func.coalesce(func.sum(WalletLedgerEntry.amount), 0)).where(
                WalletLedgerEntry.wallet_id == wallet_id,
                WalletLedgerEntry.status == status,
                WalletLedgerEntry.direction == LedgerDirection.DEBIT,
            )
        )
    ).scalar_one()
    return Decimal(credit_sum) - Decimal(debit_sum)


async def get_available_balance(db: AsyncSession, wallet_id: uuid.UUID) -> Decimal:
    """The wallet's spendable balance — the sum of POSTED ledger entries. Never a
    mutable column; always derived, per the project's core financial invariant."""
    return await _sum_entries(db, wallet_id, LedgerStatus.POSTED)


async def get_pending_balance(db: AsyncSession, wallet_id: uuid.UUID) -> Decimal:
    """Funding awaiting Operations approval — not yet usable."""
    return await _sum_entries(db, wallet_id, LedgerStatus.PENDING)


async def post_pending_credit(
    db: AsyncSession,
    *,
    wallet_id: uuid.UUID,
    amount: Decimal,
    entry_type: str,
    reference: str,
    transaction_id: uuid.UUID | None = None,
) -> WalletLedgerEntry:
    """Called when a funding request is created — reflects as Pending Balance
    until Operations approves it."""
    entry = WalletLedgerEntry(
        wallet_id=wallet_id,
        transaction_id=transaction_id,
        entry_type=entry_type,
        direction=LedgerDirection.CREDIT,
        amount=amount,
        status=LedgerStatus.PENDING,
        reference=reference,
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    await db.flush()
    return entry


async def post_entry_for_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> WalletLedgerEntry | None:
    """Moves the PENDING ledger entry for this transaction to POSTED, making
    the credit available. Idempotent by construction: once an entry is
    POSTED, calling this again finds no PENDING entry left to post."""
    entry = (
        await db.execute(
            select(WalletLedgerEntry).where(
                WalletLedgerEntry.transaction_id == transaction_id,
                WalletLedgerEntry.status == LedgerStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return None
    entry.status = LedgerStatus.POSTED
    await db.flush()
    return entry


async def reverse_entry_for_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> WalletLedgerEntry | None:
    """Used when a funding request is rejected — removes it from Pending Balance
    without ever having touched Available Balance."""
    entry = (
        await db.execute(
            select(WalletLedgerEntry).where(
                WalletLedgerEntry.transaction_id == transaction_id,
                WalletLedgerEntry.status == LedgerStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return None
    entry.status = LedgerStatus.REVERSED
    await db.flush()
    return entry

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role
from app.models.user import User
from app.models.wallet import WalletLedgerEntry
from app.schemas.wallet import WalletLedgerEntryResponse, WalletResponse
from app.security.deps import require_roles
from app.services.wallet_service import get_available_balance, get_or_create_wallet, get_pending_balance

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/me", response_model=WalletResponse)
async def get_my_wallet(
    user: User = Depends(require_roles(Role.CUSTOMER)), db: AsyncSession = Depends(get_db)
) -> dict:
    wallet = await get_or_create_wallet(db, user.id)
    available = await get_available_balance(db, wallet.id)
    pending = await get_pending_balance(db, wallet.id)
    return {
        "id": wallet.id,
        "walletId": wallet.wallet_number,
        "availableBalance": available,
        "pendingBalance": pending,
        "currency": wallet.currency,
        "updatedAt": wallet.updated_at,
    }


@router.get("/ledger", response_model=list[WalletLedgerEntryResponse])
async def get_my_ledger(
    user: User = Depends(require_roles(Role.CUSTOMER)), db: AsyncSession = Depends(get_db)
) -> list[WalletLedgerEntry]:
    wallet = await get_or_create_wallet(db, user.id)
    result = await db.execute(
        select(WalletLedgerEntry)
        .where(WalletLedgerEntry.wallet_id == wallet.id)
        .order_by(WalletLedgerEntry.created_at.desc())
    )
    return list(result.scalars().all())

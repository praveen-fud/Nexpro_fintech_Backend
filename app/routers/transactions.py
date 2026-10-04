import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role, TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction import TransactionResponse
from app.security.deps import require_roles

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=list[TransactionResponse])
async def list_transactions(
    type: TransactionType | None = None,
    status_filter: TransactionStatus | None = Query(None, alias="status"),
    limit: int = 50,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> list[Transaction]:
    query = select(Transaction).where(Transaction.customer_id == user.id)
    if type is not None:
        query = query.where(Transaction.type == type)
    if status_filter is not None:
        query = query.where(Transaction.status == status_filter)
    query = query.order_by(Transaction.created_at.desc()).limit(min(limit, 200))
    return list((await db.execute(query)).scalars().all())


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> Transaction:
    txn = (await db.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one_or_none()
    if txn is None or txn.customer_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
    return txn

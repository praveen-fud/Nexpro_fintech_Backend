from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role
from app.models.support import SupportTicket
from app.models.user import User
from app.schemas.support import CreateSupportTicketBody, SupportTicketResponse
from app.security.deps import require_roles

router = APIRouter(prefix="/support", tags=["support"])


@router.get("/tickets", response_model=list[SupportTicketResponse])
async def list_tickets(
    user: User = Depends(require_roles(Role.CUSTOMER)), db: AsyncSession = Depends(get_db)
) -> list[SupportTicket]:
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.customer_id == user.id).order_by(SupportTicket.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/tickets", response_model=SupportTicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: CreateSupportTicketBody,
    user: User = Depends(require_roles(Role.CUSTOMER)),
    db: AsyncSession = Depends(get_db),
) -> SupportTicket:
    ticket = SupportTicket(customer_id=user.id, subject=body.subject, message=body.message)
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket

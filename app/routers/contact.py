from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import enforce_subject_limit, rate_limit
from app.models.contact import ContactEnquiry
from app.models.enums import ContactEnquiryStatus, Role
from app.models.user import User
from app.schemas.contact import (
    ContactEnquiryDetail,
    ContactEnquiryRequest,
    ContactEnquiryResponse,
)
from app.security.deps import require_roles

router = APIRouter(tags=["contact"])

OPERATIONS_ROLES = (Role.OPERATIONS, Role.SUPER_ADMIN)


@router.post(
    "/contact",
    response_model=ContactEnquiryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("contact", "rate_limit_write"))],
)
async def submit_enquiry(
    request: Request,
    body: ContactEnquiryRequest,
    db: AsyncSession = Depends(get_db),
) -> ContactEnquiry:
    """Public contact form.

    Unauthenticated, so it carries two limits: a per-IP budget via the
    dependency above, and a per-email budget below. Without the second, one
    caller rotating through addresses behind a proxy could still flood the
    queue; without the first, one address could not. Both are cheap.
    """
    enforce_subject_limit(
        request, scope="contact_email", spec_attr="rate_limit_write", subject=str(body.email)
    )

    enquiry = ContactEnquiry(
        enquirer_type=body.enquirer_type,
        full_name=body.full_name,
        email=str(body.email),
        mobile_number=body.mobile_number,
        organisation=body.organisation,
        message=body.message,
        status=ContactEnquiryStatus.NEW,
    )
    db.add(enquiry)
    await db.commit()
    await db.refresh(enquiry)
    return enquiry


@router.get("/operations/contact-enquiries", response_model=list[ContactEnquiryDetail])
async def list_enquiries(
    user: User = Depends(require_roles(*OPERATIONS_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[ContactEnquiry]:
    """Back-office view of what came in through the public form."""
    result = await db.execute(
        select(ContactEnquiry).order_by(ContactEnquiry.created_at.desc()).limit(200)
    )
    return list(result.scalars().all())

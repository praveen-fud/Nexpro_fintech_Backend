"""
Super-Admin user management.

Only SUPER_ADMIN accounts can reach these endpoints.  They are the only
way to create OPERATIONS and SUPER_ADMIN accounts; CUSTOMER accounts are
always self-registered through /auth/sign-up.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import Role
from app.models.user import User
from app.schemas.admin import (
    CreateStaffUserRequest,
    StaffUserResponse,
    UpdateStaffUserRequest,
    UserListResponse,
)
from app.security.deps import require_roles
from app.security.passwords import hash_password
from app.services import audit_service

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_ONLY = (Role.SUPER_ADMIN,)


def _to_response(user: User) -> StaffUserResponse:
    return StaffUserResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        mobile_number=user.mobile_number,
        role=user.role,
        kyc_status=user.kyc_status,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    role: Role | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    query = select(User)
    if role is not None:
        query = query.where(User.role == role)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return UserListResponse(items=[_to_response(u) for u in rows], total=total)


@router.post("/users", response_model=StaffUserResponse, status_code=status.HTTP_201_CREATED)
async def create_staff_user(
    body: CreateStaffUserRequest,
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> StaffUserResponse:
    existing = (
        await db.execute(select(User).where(or_(User.email == body.email, User.mobile_number == body.mobile_number)))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or mobile number already exists.",
        )

    new_user = User(
        full_name=body.full_name,
        email=body.email,
        mobile_number=body.mobile_number,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        email_verified=True,
    )
    db.add(new_user)
    await db.flush()

    await audit_service.record_audit_event(
        db,
        actor=actor,
        action="STAFF_USER_CREATED",
        resource_type="User",
        resource_id=new_user.id,
        after_state={"role": body.role.value, "email": body.email},
    )

    await db.commit()
    await db.refresh(new_user)
    return _to_response(new_user)


@router.patch("/users/{user_id}", response_model=StaffUserResponse)
async def update_staff_user(
    user_id: uuid.UUID,
    body: UpdateStaffUserRequest,
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> StaffUserResponse:
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target.role == Role.CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer accounts cannot be modified through this endpoint.",
        )

    before = {"role": target.role.value, "isActive": target.is_active}

    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active

    await audit_service.record_audit_event(
        db,
        actor=actor,
        action="STAFF_USER_UPDATED",
        resource_type="User",
        resource_id=target.id,
        before_state=before,
        after_state={"role": target.role.value, "isActive": target.is_active},
    )

    await db.commit()
    await db.refresh(target)
    return _to_response(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_staff_user(
    user_id: uuid.UUID,
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> None:
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target.id == actor.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account.")
    if target.role == Role.CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer accounts cannot be managed through this endpoint.",
        )

    target.is_active = False

    await audit_service.record_audit_event(
        db,
        actor=actor,
        action="STAFF_USER_DEACTIVATED",
        resource_type="User",
        resource_id=target.id,
        before_state={"isActive": True},
        after_state={"isActive": False},
    )

    await db.commit()

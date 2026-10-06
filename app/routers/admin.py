"""
Super-Admin endpoints: user management, fee rules, platform limits, audit logs,
dashboard overview, and customer list.

Only SUPER_ADMIN accounts can reach these endpoints. CUSTOMER accounts are
always self-registered through /auth/sign-up.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.config import FeeRule, PlatformLimit
from app.models.enums import FundingStatus, KycStatus, Role
from app.models.user import User
from app.models.wallet import Wallet
from app.models.funding import FundingRequest
from app.schemas.admin import (
    AdminCustomerListResponse,
    AdminCustomerResponse,
    AdminOverviewResponse,
    AuditLogListResponse,
    AuditLogResponse,
    CreateStaffUserRequest,
    FeeRuleResponse,
    PlatformLimitResponse,
    StaffUserResponse,
    UpdateFeeRuleRequest,
    UpdatePlatformLimitRequest,
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


# ── Overview Dashboard ─────────────────────────────────────────────────────────

@router.get("/overview", response_model=AdminOverviewResponse)
async def get_overview(
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewResponse:
    total_customers = (
        await db.execute(select(func.count(User.id)).where(User.role == Role.CUSTOMER))
    ).scalar_one()

    active_customers = (
        await db.execute(
            select(func.count(User.id)).where(User.role == Role.CUSTOMER, User.is_active == True)  # noqa: E712
        )
    ).scalar_one()

    total_funding_volume = (
        await db.execute(
            select(func.coalesce(func.sum(FundingRequest.requested_amount), 0)).where(
                FundingRequest.status == FundingStatus.APPROVED
            )
        )
    ).scalar_one()

    pending_funding = (
        await db.execute(
            select(func.coalesce(func.sum(FundingRequest.requested_amount), 0)).where(
                FundingRequest.status.in_([FundingStatus.PENDING, FundingStatus.UNDER_REVIEW])
            )
        )
    ).scalar_one()

    kyc_pending = (
        await db.execute(
            select(func.count(User.id)).where(
                User.kyc_status.in_([KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW])
            )
        )
    ).scalar_one()

    total_wallets = (await db.execute(select(func.count(Wallet.id)))).scalar_one()

    return AdminOverviewResponse(
        total_customers=total_customers,
        active_customers=active_customers,
        total_funding_volume=Decimal(total_funding_volume),
        pending_funding=Decimal(pending_funding),
        kyc_pending=kyc_pending,
        total_wallets=total_wallets,
    )


# ── Customers ──────────────────────────────────────────────────────────────────

@router.get("/customers", response_model=AdminCustomerListResponse)
async def list_customers(
    search: str | None = None,
    kyc_status: KycStatus | None = None,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> AdminCustomerListResponse:
    query = select(User).where(User.role == Role.CUSTOMER)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )
    if kyc_status is not None:
        query = query.where(User.kyc_status == kyc_status)
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return AdminCustomerListResponse(
        items=[
            AdminCustomerResponse(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
                mobile_number=u.mobile_number,
                kyc_status=u.kyc_status,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in rows
        ],
        total=total,
    )


# ── Fee Rules ──────────────────────────────────────────────────────────────────

def _fee_rule_to_response(rule: FeeRule) -> FeeRuleResponse:
    return FeeRuleResponse(
        id=rule.id,
        method=rule.method,
        fee_type=rule.fee_type,
        fixed_amount=rule.fixed_amount,
        percentage=rule.percentage,
        min_fee=rule.min_fee,
        max_fee=rule.max_fee,
        is_enabled=rule.is_enabled,
        updated_at=rule.updated_at,
    )


@router.get("/fee-rules", response_model=list[FeeRuleResponse])
async def list_fee_rules(
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> list[FeeRuleResponse]:
    rules = (await db.execute(select(FeeRule).order_by(FeeRule.method))).scalars().all()
    return [_fee_rule_to_response(r) for r in rules]


@router.patch("/fee-rules/{rule_id}", response_model=FeeRuleResponse)
async def update_fee_rule(
    rule_id: uuid.UUID,
    body: UpdateFeeRuleRequest,
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> FeeRuleResponse:
    rule = (await db.execute(select(FeeRule).where(FeeRule.id == rule_id))).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee rule not found.")

    before = {
        "feeType": rule.fee_type,
        "fixedAmount": str(rule.fixed_amount),
        "percentage": str(rule.percentage),
        "minFee": str(rule.min_fee),
        "maxFee": str(rule.max_fee),
        "isEnabled": rule.is_enabled,
    }

    if body.fee_type is not None:
        rule.fee_type = body.fee_type
    if body.fixed_amount is not None:
        rule.fixed_amount = body.fixed_amount
    if body.percentage is not None:
        rule.percentage = body.percentage
    if body.min_fee is not None:
        rule.min_fee = body.min_fee
    if body.max_fee is not None:
        rule.max_fee = body.max_fee
    if body.is_enabled is not None:
        rule.is_enabled = body.is_enabled

    await audit_service.record_audit_event(
        db,
        actor=actor,
        action="FEE_RULE_UPDATED",
        resource_type="FeeRule",
        resource_id=rule.id,
        before_state=before,
        after_state={
            "feeType": rule.fee_type,
            "fixedAmount": str(rule.fixed_amount),
            "percentage": str(rule.percentage),
            "isEnabled": rule.is_enabled,
        },
    )

    await db.commit()
    await db.refresh(rule)
    return _fee_rule_to_response(rule)


# ── Platform Limits ────────────────────────────────────────────────────────────

def _limit_to_response(limit: PlatformLimit) -> PlatformLimitResponse:
    return PlatformLimitResponse(
        id=limit.id,
        scope=limit.scope,
        per_transaction=limit.per_transaction,
        daily=limit.daily,
        monthly=limit.monthly,
        updated_at=limit.updated_at,
    )


@router.get("/platform-limits", response_model=list[PlatformLimitResponse])
async def list_platform_limits(
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> list[PlatformLimitResponse]:
    limits = (await db.execute(select(PlatformLimit).order_by(PlatformLimit.scope))).scalars().all()
    return [_limit_to_response(lim) for lim in limits]


@router.patch("/platform-limits/{limit_id}", response_model=PlatformLimitResponse)
async def update_platform_limit(
    limit_id: uuid.UUID,
    body: UpdatePlatformLimitRequest,
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> PlatformLimitResponse:
    limit = (await db.execute(select(PlatformLimit).where(PlatformLimit.id == limit_id))).scalar_one_or_none()
    if limit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform limit not found.")

    before = {
        "perTransaction": str(limit.per_transaction),
        "daily": str(limit.daily),
        "monthly": str(limit.monthly),
    }

    if body.per_transaction is not None:
        limit.per_transaction = body.per_transaction
    if body.daily is not None:
        limit.daily = body.daily
    if body.monthly is not None:
        limit.monthly = body.monthly

    await audit_service.record_audit_event(
        db,
        actor=actor,
        action="PLATFORM_LIMIT_UPDATED",
        resource_type="PlatformLimit",
        resource_id=limit.id,
        before_state=before,
        after_state={
            "perTransaction": str(limit.per_transaction),
            "daily": str(limit.daily),
            "monthly": str(limit.monthly),
        },
    )

    await db.commit()
    await db.refresh(limit)
    return _limit_to_response(limit)


# ── Audit Logs ─────────────────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: str | None = None,
    resource_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    actor: User = Depends(require_roles(*ADMIN_ONLY)),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        query = query.where(AuditLog.resource_type.ilike(f"%{resource_type}%"))
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=log.id,
                actor_id=log.actor_id,
                actor_role=log.actor_role,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                before_state=log.before_state,
                after_state=log.after_state,
                reason=log.reason,
                created_at=log.created_at,
            )
            for log in rows
        ],
        total=total,
    )

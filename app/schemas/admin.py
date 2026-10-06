from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import EmailStr, Field

from app.models.enums import FundingMethod, KycStatus, Role
from app.schemas.common import CamelModel

# Only these two roles can be created through the admin portal.
# CUSTOMER accounts are always self-registered.
STAFF_ROLES = {Role.OPERATIONS, Role.SUPER_ADMIN}


class CreateStaffUserRequest(CamelModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    mobile_number: str = Field(pattern=r"^[6-9]\d{9}$")
    role: Role
    password: str = Field(min_length=8, max_length=128)

    def model_post_init(self, __context) -> None:
        if self.role not in STAFF_ROLES:
            raise ValueError("Only OPERATIONS and SUPER_ADMIN roles can be created here.")


class UpdateStaffUserRequest(CamelModel):
    role: Role | None = None
    is_active: bool | None = None

    def model_post_init(self, __context) -> None:
        if self.role is not None and self.role not in STAFF_ROLES:
            raise ValueError("Only OPERATIONS and SUPER_ADMIN roles are permitted.")


class StaffUserResponse(CamelModel):
    id: UUID
    full_name: str
    email: str
    mobile_number: str
    role: Role
    kyc_status: KycStatus
    is_active: bool
    created_at: datetime


class UserListResponse(CamelModel):
    items: list[StaffUserResponse]
    total: int


# ── Admin Overview ─────────────────────────────────────────────────────────────

class AdminOverviewResponse(CamelModel):
    total_customers: int
    active_customers: int
    total_funding_volume: Decimal
    pending_funding: Decimal
    kyc_pending: int
    total_wallets: int


# ── Admin Customers ────────────────────────────────────────────────────────────

class AdminCustomerResponse(CamelModel):
    id: UUID
    full_name: str
    email: str
    mobile_number: str
    kyc_status: KycStatus
    is_active: bool
    created_at: datetime


class AdminCustomerListResponse(CamelModel):
    items: list[AdminCustomerResponse]
    total: int


# ── Fee Rules ──────────────────────────────────────────────────────────────────

class FeeRuleResponse(CamelModel):
    id: UUID
    method: FundingMethod
    fee_type: str
    fixed_amount: Decimal
    percentage: Decimal
    min_fee: Decimal
    max_fee: Decimal
    is_enabled: bool
    updated_at: datetime


class UpdateFeeRuleRequest(CamelModel):
    fee_type: str | None = Field(None, pattern="^(FIXED|PERCENTAGE)$")
    fixed_amount: Decimal | None = Field(None, ge=0)
    percentage: Decimal | None = Field(None, ge=0, le=100)
    min_fee: Decimal | None = Field(None, ge=0)
    max_fee: Decimal | None = Field(None, ge=0)
    is_enabled: bool | None = None


# ── Platform Limits ────────────────────────────────────────────────────────────

class PlatformLimitResponse(CamelModel):
    id: UUID
    scope: str
    per_transaction: Decimal
    daily: Decimal
    monthly: Decimal
    updated_at: datetime


class UpdatePlatformLimitRequest(CamelModel):
    per_transaction: Decimal | None = Field(None, gt=0)
    daily: Decimal | None = Field(None, gt=0)
    monthly: Decimal | None = Field(None, gt=0)


# ── Audit Logs ─────────────────────────────────────────────────────────────────

class AuditLogResponse(CamelModel):
    id: UUID
    actor_id: UUID
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    before_state: dict | None
    after_state: dict | None
    reason: str | None
    created_at: datetime


class AuditLogListResponse(CamelModel):
    items: list[AuditLogResponse]
    total: int

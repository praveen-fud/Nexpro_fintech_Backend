from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from app.models.enums import KycStatus, Role
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

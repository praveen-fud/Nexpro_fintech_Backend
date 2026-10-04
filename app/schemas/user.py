import uuid
from datetime import datetime

from app.models.enums import KycStatus, Role
from app.schemas.common import CamelModel


class UserResponse(CamelModel):
    id: uuid.UUID
    full_name: str
    email: str
    mobile_number: str
    role: Role
    kyc_status: KycStatus
    created_at: datetime

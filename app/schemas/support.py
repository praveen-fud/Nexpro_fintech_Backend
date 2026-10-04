import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import SupportTicketStatus
from app.schemas.common import CamelModel


class CreateSupportTicketBody(CamelModel):
    subject: str = Field(min_length=2, max_length=200)
    message: str = Field(min_length=2, max_length=2000)


class SupportTicketResponse(CamelModel):
    id: uuid.UUID
    subject: str
    status: SupportTicketStatus
    created_at: datetime

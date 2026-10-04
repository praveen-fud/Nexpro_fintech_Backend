import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User


async def record_audit_event(
    db: AsyncSession,
    *,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id,
        actor_role=actor.role.value,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        before_state=before_state,
        after_state=after_state,
        reason=reason,
    )
    db.add(entry)
    await db.flush()
    return entry

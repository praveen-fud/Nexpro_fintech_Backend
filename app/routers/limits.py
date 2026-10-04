from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.config import PlatformLimit
from app.models.enums import Role
from app.models.user import User
from app.schemas.limits import LimitsResponse
from app.security.deps import require_roles

router = APIRouter(prefix="/limits", tags=["limits"])

_DEFAULT_LIMITS = LimitsResponse(per_transaction=200000, daily=500000, monthly=2000000)


@router.get("/me", response_model=LimitsResponse)
async def get_my_limits(
    user: User = Depends(require_roles(Role.CUSTOMER)), db: AsyncSession = Depends(get_db)
) -> LimitsResponse:
    limit = (await db.execute(select(PlatformLimit).where(PlatformLimit.scope == "GLOBAL"))).scalar_one_or_none()
    if limit is None:
        return _DEFAULT_LIMITS
    return LimitsResponse(per_transaction=limit.per_transaction, daily=limit.daily, monthly=limit.monthly)

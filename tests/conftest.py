import os

# Must happen before any `app.*` module is imported, since app.core.database
# builds its engine from settings at import time.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["JWT_SECRET"] = "test-secret-key-at-least-32-bytes-long-for-hs256"

from pathlib import Path  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import KycStatus, Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

TEST_PASSWORD = "Password123"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # Each test logs in several times; start every test with a clean rate-limit
    # window so the production limit (10 logins/min) doesn't trip the suite.
    from app.core.rate_limiter import limiter

    limiter.reset()
    yield
    await engine.dispose()
    db_file = Path("test.db")
    if db_file.exists():
        db_file.unlink()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def create_user(
    *, full_name: str, email: str, mobile: str, role: Role = Role.CUSTOMER, kyc_status: KycStatus = KycStatus.APPROVED
) -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            full_name=full_name,
            email=email,
            mobile_number=mobile,
            password_hash=hash_password(TEST_PASSWORD),
            role=role,
            kyc_status=kyc_status,
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def login(client: AsyncClient, identifier: str) -> str:
    res = await client.post("/api/v1/auth/login", json={"identifier": identifier, "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def bank_payload(amount: int = 5000) -> dict:
    """A valid bank-transfer funding body with a unique UTR (UTRs are unique)."""
    import uuid

    return {
        "method": "BANK_TRANSFER",
        "amount": amount,
        "paymentDetails": {"referenceNumber": uuid.uuid4().hex[:16].upper()},
    }

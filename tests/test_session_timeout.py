"""Idle-timeout session policy: 15 min of user inactivity ends the session;
only the explicit heartbeat (real user activity) extends it; refreshing and
background API calls do not."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import Role
from app.models.user import AuthSession
from tests.conftest import auth_headers, create_user, login

EMAIL = "idle@nexprotest.com"


async def _setup(client):
    await create_user(full_name="Idle User", email=EMAIL, mobile="9000000071", role=Role.CUSTOMER)
    return await login(client, EMAIL)


async def _age_session(*, idle_minutes: float = 0, started_hours_ago: float = 0) -> None:
    async with AsyncSessionLocal() as db:
        sess = (await db.execute(select(AuthSession))).scalars().one()
        now = datetime.now(UTC)
        if idle_minutes:
            sess.last_active_at = now - timedelta(minutes=idle_minutes)
        if started_hours_ago:
            sess.started_at = now - timedelta(hours=started_hours_ago)
        await db.commit()


async def test_active_session_works(client):
    token = await _setup(client)
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 200


async def test_idle_session_is_rejected(client):
    token = await _setup(client)
    await _age_session(idle_minutes=16)
    res = await client.get("/api/v1/users/me", headers=auth_headers(token))
    assert res.status_code == 401
    assert "inactivity" in res.json()["message"].lower() or "inactivity" in str(res.json()).lower()


async def test_just_under_limit_still_works(client):
    token = await _setup(client)
    await _age_session(idle_minutes=14.5)
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 200


async def test_refresh_rejected_after_idle(client):
    await _setup(client)
    await _age_session(idle_minutes=16)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_does_not_extend_idle_window(client):
    await _setup(client)
    await _age_session(idle_minutes=10)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    async with AsyncSessionLocal() as db:
        sess = (await db.execute(select(AuthSession))).scalars().one()
        last = sess.last_active_at if sess.last_active_at.tzinfo else sess.last_active_at.replace(tzinfo=UTC)
        assert datetime.now(UTC) - last > timedelta(minutes=9)


async def test_api_calls_do_not_extend_idle_window(client):
    token = await _setup(client)
    await _age_session(idle_minutes=10)
    await client.get("/api/v1/users/me", headers=auth_headers(token))
    async with AsyncSessionLocal() as db:
        sess = (await db.execute(select(AuthSession))).scalars().one()
        last = sess.last_active_at if sess.last_active_at.tzinfo else sess.last_active_at.replace(tzinfo=UTC)
        assert datetime.now(UTC) - last > timedelta(minutes=9)


async def test_heartbeat_extends_session(client):
    token = await _setup(client)
    await _age_session(idle_minutes=14)
    assert (await client.post("/api/v1/auth/heartbeat", headers=auth_headers(token))).status_code == 204
    await _age_session(idle_minutes=0)  # no-op; heartbeat reset the clock
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 200


async def test_heartbeat_cannot_revive_expired_session(client):
    token = await _setup(client)
    await _age_session(idle_minutes=16)
    assert (await client.post("/api/v1/auth/heartbeat", headers=auth_headers(token))).status_code == 401


async def test_absolute_cap_ends_even_active_session(client):
    token = await _setup(client)
    await client.post("/api/v1/auth/heartbeat", headers=auth_headers(token))
    await _age_session(started_hours_ago=13)
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 401


async def test_logout_revokes_session_for_existing_access_token(client):
    token = await _setup(client)
    await client.post("/api/v1/auth/logout")
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 401


async def test_token_survives_rotation_within_session(client):
    token = await _setup(client)
    new_token = (await client.post("/api/v1/auth/refresh")).json()["accessToken"]
    assert (await client.get("/api/v1/users/me", headers=auth_headers(new_token))).status_code == 200
    assert (await client.get("/api/v1/users/me", headers=auth_headers(token))).status_code == 200

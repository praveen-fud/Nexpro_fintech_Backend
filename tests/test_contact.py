"""The public contact form.

Unauthenticated writes get their own tests: this is the only endpoint on the
platform that lets an anonymous caller create a database row, so the bounds
on it matter more than the happy path does.
"""

import pytest

from app.models.enums import Role
from tests.conftest import auth_headers, create_user, login

VALID = {
    "enquirerType": "BUSINESS",
    "fullName": "Asha Menon",
    "email": "asha@example.com",
    "mobileNumber": "9876543210",
    "organisation": "Example Pvt Ltd",
    "message": "We would like to understand your payout timelines and pricing.",
}


@pytest.mark.asyncio
async def test_anyone_can_submit_an_enquiry(client):
    res = await client.post("/api/v1/contact", json=VALID)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "NEW"
    assert "id" in body


@pytest.mark.asyncio
async def test_acknowledgement_does_not_echo_the_submission(client):
    """An unauthenticated endpoint has no reason to reflect input back."""
    res = await client.post("/api/v1/contact", json=VALID)
    body = res.json()
    assert "email" not in body
    assert "message" not in body
    assert "fullName" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mobileNumber", "1234567890"),  # does not start 6-9
        ("mobileNumber", "98765"),
        ("email", "not-an-email"),
        ("message", "too short"),
        ("fullName", "A"),
        ("fullName", "<script>alert(1)</script>"),
        ("enquirerType", "ADMIN"),
    ],
)
async def test_malformed_submissions_are_rejected(client, field, value):
    res = await client.post("/api/v1/contact", json={**VALID, field: value})
    assert 400 <= res.status_code < 500, f"{field}={value!r} produced {res.status_code}"


@pytest.mark.asyncio
async def test_oversized_message_is_rejected(client):
    res = await client.post("/api/v1/contact", json={**VALID, "message": "A" * 5000})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_control_characters_are_stripped(client):
    """Staff read these in the back office; smuggled newlines and escape
    sequences have no business surviving the trip."""
    res = await client.post(
        "/api/v1/contact",
        json={**VALID, "fullName": "Asha\r\n\x1b[31mMenon"},
    )
    # Either cleaned and accepted, or rejected — never stored with the escape.
    if res.status_code == 201:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.contact import ContactEnquiry

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(ContactEnquiry))).scalars().first()
            assert "\x1b" not in row.full_name
            assert "\r" not in row.full_name
    else:
        assert 400 <= res.status_code < 500


@pytest.mark.asyncio
async def test_back_office_listing_requires_staff(client):
    await create_user(full_name="Nosy", email="nosy@test.local", mobile="9000000801")
    token = await login(client, "nosy@test.local")

    res = await client.get("/api/v1/operations/contact-enquiries", headers=auth_headers(token))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_back_office_listing_is_readable_by_operations(client):
    await client.post("/api/v1/contact", json=VALID)
    await create_user(
        full_name="Ops", email="ops-contact@test.local", mobile="9000000802", role=Role.OPERATIONS
    )
    token = await login(client, "ops-contact@test.local")

    res = await client.get("/api/v1/operations/contact-enquiries", headers=auth_headers(token))
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    # Staff need the actual content, unlike the public acknowledgement.
    assert rows[0]["email"] == "asha@example.com"
    assert rows[0]["message"].startswith("We would like")


@pytest.mark.asyncio
async def test_unauthenticated_cannot_read_enquiries(client):
    res = await client.get("/api/v1/operations/contact-enquiries")
    assert res.status_code == 401

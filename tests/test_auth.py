"""Email/SMS OTP verification is disabled for now (no real provider
connected) — sign_up marks accounts verified immediately so login works
right away. This just confirms that actually holds; the /verify-otp
endpoint and login's email_verified check are left in place (dormant) for
when real verification is reinstated.
"""


async def test_login_succeeds_immediately_after_sign_up(client):
    sign_up_res = await client.post(
        "/api/v1/auth/sign-up",
        json={
            "fullName": "New Customer",
            "email": "newcustomer@nexprotest.com",
            "mobileNumber": "9000000099",
            "password": "Password123",
            "confirmPassword": "Password123",
            "acceptedTerms": True,
        },
    )
    assert sign_up_res.status_code == 201, sign_up_res.text
    assert sign_up_res.json()["kycStatus"] == "NOT_STARTED"

    login_res = await client.post(
        "/api/v1/auth/login", json={"identifier": "newcustomer@nexprotest.com", "password": "Password123"}
    )
    assert login_res.status_code == 200, login_res.text


async def test_refresh_token_rotates_successfully(client):
    """Regression test: RefreshToken.expires_at comes back tz-naive from
    SQLite even though the column is DateTime(timezone=True), which used to
    crash this comparison against datetime.now(UTC) with "can't compare
    offset-naive and offset-aware datetimes" — i.e. every refresh (every
    page reload) was broken in local/SQLite dev."""
    await client.post(
        "/api/v1/auth/sign-up",
        json={
            "fullName": "Refresh Test",
            "email": "refreshtest@nexprotest.com",
            "mobileNumber": "9000000098",
            "password": "Password123",
            "confirmPassword": "Password123",
            "acceptedTerms": True,
        },
    )
    login_res = await client.post(
        "/api/v1/auth/login", json={"identifier": "refreshtest@nexprotest.com", "password": "Password123"}
    )
    assert login_res.status_code == 200, login_res.text

    refresh_res = await client.post("/api/v1/auth/refresh")
    assert refresh_res.status_code == 200, refresh_res.text
    assert refresh_res.json()["accessToken"]

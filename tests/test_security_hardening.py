"""
Tests for the hardening added in the security review.

These assert on attacker-visible behaviour (status codes, headers, what gets
persisted) rather than on implementation details, so they keep their value if
the internals are refactored.
"""

import pytest
from sqlalchemy import update as sa_update

from app.core.config import ConfigurationError, Settings
from app.core.database import AsyncSessionLocal
from app.core.rate_limit import SlidingWindowLimiter, Window, reset_all
from app.models.enums import Role
from app.models.user import User as UserModel
from app.services.payment_details import sanitize_payment_details
from tests.conftest import PNG_1PX, auth_headers, create_user, login

PROD_BASE = {
    "environment": "production",
    "jwt_secret": "x" * 48,
    "database_url": "postgresql+asyncpg://u:p@h/db",
    "cors_origins": ["https://app.nexpro.test"],
    "trusted_hosts": ["api.nexpro.test"],
}


# --------------------------------------------------------------------------
# Configuration must fail closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("no secret", {"jwt_secret": ""}),
        ("publicly known secret", {"jwt_secret": "insecure-dev-secret-change-me-before-production-use"}),
        ("secret too short", {"jwt_secret": "short"}),
        ("sqlite in production", {"database_url": "sqlite+aiosqlite:///./dev.db"}),
        ("cors wildcard", {"cors_origins": ["*"]}),
        ("plaintext cors origin", {"cors_origins": ["http://app.nexpro.test"]}),
        ("wildcard trusted host", {"trusted_hosts": ["*"]}),
        ("alg none", {"jwt_algorithm": "none"}),
    ],
)
def test_production_refuses_unsafe_configuration(label, overrides):
    with pytest.raises(ConfigurationError):
        Settings(_env_file=None, **{**PROD_BASE, **overrides})


def test_safe_production_configuration_boots():
    settings = Settings(_env_file=None, **PROD_BASE)
    assert settings.is_production
    assert settings.cookie_secure is True
    # Docs are off in production unless explicitly opted into.
    assert settings.docs_enabled is False


def test_docs_on_outside_production():
    assert Settings(_env_file=None, environment="development").docs_enabled is True


def test_cors_origins_accept_comma_separated_form():
    # Deploy dashboards make JSON easy to typo; both forms must work.
    settings = Settings(_env_file=None, cors_origins="https://a.test, https://b.test")
    assert settings.cors_origins == ["https://a.test", "https://b.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.test, https://b.test", ["https://a.test", "https://b.test"]),
        ('["https://a.test", "https://b.test"]', ["https://a.test", "https://b.test"]),
        ("https://a.test", ["https://a.test"]),
    ],
)
def test_list_settings_parse_from_the_environment(monkeypatch, raw, expected):
    """Parse the way a deploy actually sets them: as environment variables.

    Passing kwargs to Settings() bypasses the env source entirely, so the
    test above cannot catch the failure mode that matters — pydantic-settings
    json.loads()-ing the raw value before any validator runs and raising
    SettingsError at boot. These fields are annotated NoDecode precisely to
    stop that, and this is what proves it.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)
    monkeypatch.setenv("TRUSTED_HOSTS", raw)
    settings = Settings(_env_file=None)
    assert settings.cors_origins == expected
    assert settings.trusted_hosts == expected


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_headers_present_on_every_response(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert "default-src 'none'" in res.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_headers_also_present_on_error_responses(client):
    # An unauthenticated call still has to carry the headers — error paths
    # are where middleware ordering bugs usually show up.
    res = await client.get("/api/v1/users/me")
    assert res.status_code == 401
    assert res.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_hsts_absent_outside_production(client):
    # Sending HSTS from a dev server over http would pin the browser.
    res = await client.get("/health")
    assert "strict-transport-security" not in res.headers


# --------------------------------------------------------------------------
# Rate limiting and lockout
# --------------------------------------------------------------------------


def test_sliding_window_allows_then_blocks():
    limiter = SlidingWindowLimiter()
    window = Window.parse("3/60")
    results = [limiter.check(scope="t", key="k", window=window)[0] for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_sliding_window_isolates_keys_and_scopes():
    limiter = SlidingWindowLimiter()
    window = Window.parse("1/60")
    assert limiter.check(scope="a", key="k1", window=window)[0] is True
    assert limiter.check(scope="a", key="k1", window=window)[0] is False
    # A different client is unaffected...
    assert limiter.check(scope="a", key="k2", window=window)[0] is True
    # ...and so is a different endpoint budget.
    assert limiter.check(scope="b", key="k1", window=window)[0] is True


def test_sliding_window_reports_retry_after():
    limiter = SlidingWindowLimiter()
    window = Window.parse("1/60")
    limiter.check(scope="t", key="k", window=window)
    allowed, retry_after = limiter.check(scope="t", key="k", window=window)
    assert allowed is False
    assert 1 <= retry_after <= 61


@pytest.mark.parametrize("spec", ["", "abc", "10", "0/60", "10/0", "-1/60"])
def test_invalid_rate_limit_specs_are_rejected(spec):
    with pytest.raises(ValueError):
        Window.parse(spec)


@pytest.mark.asyncio
async def test_repeated_bad_passwords_eventually_get_429(client):
    await create_user(full_name="Target", email="target@test.local", mobile="9000000101")
    reset_all()

    statuses = []
    for _ in range(14):
        res = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "target@test.local", "password": "WrongPassword!1"},
        )
        statuses.append(res.status_code)

    assert 401 in statuses, statuses
    assert 429 in statuses, "brute force was never throttled"
    # Once throttled, the client is told when to come back.
    throttled = next(s for s in statuses if s == 429)
    assert throttled == 429


@pytest.mark.asyncio
async def test_lockout_does_not_leak_whether_the_account_exists(client):
    reset_all()
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "nobody@test.local", "password": "WrongPassword!1"},
    )
    await create_user(full_name="Real", email="real@test.local", mobile="9000000102")
    reset_all()
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "real@test.local", "password": "WrongPassword!1"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["message"] == wrong.json()["message"]


@pytest.mark.asyncio
async def test_deactivated_account_is_indistinguishable_from_bad_password(client):
    user = await create_user(full_name="Gone", email="gone@test.local", mobile="9000000103")

    async with AsyncSessionLocal() as session:
        await session.execute(sa_update(UserModel).where(UserModel.id == user.id).values(is_active=False))
        await session.commit()

    reset_all()
    res = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "gone@test.local", "password": "TestPassw0rd!2026"},
    )
    assert res.status_code == 401
    assert res.json()["message"] == "Incorrect email/mobile number or password."


# --------------------------------------------------------------------------
# Password policy
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "password",
    [
        "short1!A",        # under 10 characters
        "password123",     # common
        "1234567890123",   # digits only
        "alllowercase",    # only one character class
    ],
)
async def test_weak_passwords_are_rejected_at_signup(client, password):
    res = await client.post(
        "/api/v1/auth/sign-up",
        json={
            "fullName": "Weak Password",
            "email": f"weak{abs(hash(password)) % 10000}@nexprotest.com",
            "mobileNumber": "9000000201",
            "password": password,
            "confirmPassword": password,
            "acceptedTerms": True,
        },
    )
    assert res.status_code == 422, res.text
    assert "password" in str(res.json()["fieldErrors"]).lower()


@pytest.mark.asyncio
async def test_signup_cannot_self_assign_a_privileged_role(client):
    # Role is never read from the request body. If it were, this would be the
    # whole attack: register yourself as SUPER_ADMIN.
    res = await client.post(
        "/api/v1/auth/sign-up",
        json={
            "fullName": "Sneaky Admin",
            "email": "sneaky@nexprotest.com",
            "mobileNumber": "9000000202",
            "password": "LegitPassw0rd!99",
            "confirmPassword": "LegitPassw0rd!99",
            "acceptedTerms": True,
            "role": "SUPER_ADMIN",
            "kycStatus": "APPROVED",
            "isActive": True,
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["role"] == Role.CUSTOMER.value
    # The KYC status must not be mass-assignable either.
    assert res.json()["kycStatus"] == "NOT_STARTED"


# --------------------------------------------------------------------------
# Dormant OTP endpoint
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_endpoint_is_not_reachable_by_default(client):
    # Unauthenticated, accepts any code, and flips account state — it must
    # stay switched off.
    res = await client.post(
        "/api/v1/auth/verify-otp",
        json={"email": "anyone@nexprotest.com", "code": "123456"},
    )
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "1e999", "abc", "", "100.001"])
async def test_funding_rejects_non_finite_and_overprecise_amounts(client, amount):
    await create_user(full_name="Payer", email="payer@test.local", mobile="9000000301")
    token = await login(client, "payer@test.local")

    res = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": amount, "paymentDetails": {}},
        headers=auth_headers(token),
    )
    # The important part is that it is a clean 4xx, never a 500.
    assert 400 <= res.status_code < 500, f"{amount!r} produced {res.status_code}"


@pytest.mark.asyncio
async def test_kyc_submit_rejects_malformed_personal_info(client):
    await create_user(
        full_name="Kyc Tester", email="kyctest@test.local", mobile="9000000302", kyc_status="NOT_STARTED"
    )
    token = await login(client, "kyctest@test.local")

    res = await client.post(
        "/api/v1/kyc/submit",
        data={
            # Over-long address (column is String(255)), bad PIN, future DOB.
            "personalInfo": (
                '{"dateOfBirth":"2099-01-01","address":"' + "A" * 900 + '",'
                '"city":"Pune","state":"MH","pinCode":"abc"}'
            ),
            "bankAccount": (
                '{"accountHolderName":"Test User","accountNumber":"123456789012",'
                '"confirmAccountNumber":"123456789012","bankName":"HDFC Bank",'
                '"ifsc":"HDFC0000001"}'
            ),
        },
        files={
            "idProof": ("id.png", PNG_1PX, "image/png"),
            "addressProof": ("address.png", PNG_1PX, "image/png"),
            "panCard": ("pan.png", PNG_1PX, "image/png"),
        },
        headers=auth_headers(token),
    )
    assert res.status_code == 422, res.text
    errors = str(res.json()["fieldErrors"])
    assert "pinCode" in errors or "dateOfBirth" in errors or "address" in errors


@pytest.mark.asyncio
async def test_kyc_submit_requires_matching_account_numbers(client):
    await create_user(
        full_name="Mismatch", email="mismatch@test.local", mobile="9000000303", kyc_status="NOT_STARTED"
    )
    token = await login(client, "mismatch@test.local")

    res = await client.post(
        "/api/v1/kyc/submit",
        data={
            "personalInfo": (
                '{"dateOfBirth":"1990-05-05","address":"12 Main Street","city":"Pune",'
                '"state":"MH","pinCode":"411001"}'
            ),
            "bankAccount": (
                '{"accountHolderName":"Test User","accountNumber":"123456789012",'
                '"confirmAccountNumber":"999999999999","bankName":"HDFC Bank",'
                '"ifsc":"HDFC0000001"}'
            ),
        },
        files={
            "idProof": ("id.png", PNG_1PX, "image/png"),
            "addressProof": ("address.png", PNG_1PX, "image/png"),
            "panCard": ("pan.png", PNG_1PX, "image/png"),
        },
        headers=auth_headers(token),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_upload_rejects_a_file_whose_bytes_are_not_an_image(client):
    await create_user(full_name="Liar", email="liar@test.local", mobile="9000000304", kyc_status="NOT_STARTED")
    token = await login(client, "liar@test.local")

    html_payload = b"<html><script>alert(document.domain)</script></html>"
    res = await client.post(
        "/api/v1/kyc/submit",
        data={
            "personalInfo": (
                '{"dateOfBirth":"1990-05-05","address":"12 Main Street","city":"Pune",'
                '"state":"MH","pinCode":"411001"}'
            ),
            "bankAccount": (
                '{"accountHolderName":"Test User","accountNumber":"123456789012",'
                '"confirmAccountNumber":"123456789012","bankName":"HDFC Bank",'
                '"ifsc":"HDFC0000001"}'
            ),
        },
        files={
            # Claims to be a PNG, is actually HTML.
            "idProof": ("payload.png", html_payload, "image/png"),
            "addressProof": ("address.png", PNG_1PX, "image/png"),
            "panCard": ("pan.png", PNG_1PX, "image/png"),
        },
        headers=auth_headers(token),
    )
    assert res.status_code == 400, res.text


# --------------------------------------------------------------------------
# Card data must never be persisted
# --------------------------------------------------------------------------


def test_payment_details_drops_card_fields():
    dirty = {
        "cardNumber": "4111111111111111",
        "card_no": "4111111111111111",
        "cvv": "123",
        "expiryMonth": "12",
        "cardholderName": "A Person",
        "password": "hunter2",
        "otp": "123456",
        "upiId": "someone@bank",
    }
    clean = sanitize_payment_details(dirty)
    assert clean == {"upiId": "someone@bank"}
    flat = str(clean)
    assert "4111111111111111" not in flat
    assert "123" not in flat or "cvv" not in flat


def test_payment_details_redacts_a_pan_hidden_in_a_free_text_field():
    clean = sanitize_payment_details({"note": "paid with 4111 1111 1111 1111 today"})
    assert "4111" not in clean["note"].replace("[redacted-pan:1111]", "")
    assert "redacted-pan" in clean["note"]


def test_payment_details_keeps_a_genuine_reference_that_looks_numeric():
    # A long digit string that fails Luhn is a reference, not a card.
    clean = sanitize_payment_details({"referenceNumber": "1234567890123"})
    assert clean["referenceNumber"] == "1234567890123"


def test_payment_details_rejects_an_unmasked_masked_card():
    assert sanitize_payment_details({"maskedCard": "4111111111111111"}) == {}
    assert sanitize_payment_details({"maskedCard": "XXXXXXXXXXXX4242"}) == {"maskedCard": "XXXXXXXXXXXX4242"}


def test_payment_details_ignores_nested_structures_and_bounds_size():
    clean = sanitize_payment_details(
        {"note": "x" * 5000, "reference": {"nested": "object"}, "upiId": ["a", "b"]}
    )
    assert len(clean["note"]) <= 128
    assert "reference" not in clean
    assert "upiId" not in clean


def test_payment_details_handles_non_dict_input():
    assert sanitize_payment_details(None) == {}
    assert sanitize_payment_details("just a string") == {}
    assert sanitize_payment_details([1, 2, 3]) == {}


# --------------------------------------------------------------------------
# Request size cap
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_request_body_is_rejected(client):
    settings_cap = 10 * 1024 * 1024
    res = await client.post(
        "/api/v1/auth/login",
        content=b"x" * (settings_cap + 1024),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 413, res.status_code

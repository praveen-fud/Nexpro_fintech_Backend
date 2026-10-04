"""CRITICAL TESTS:
- A customer cannot fund their wallet until Operations approves their KYC —
  the funding-creation endpoint must enforce this server-side, not just hide
  the UI entry point.
- Only Operations/Super Admin can review KYC profiles.
- A KYC profile cannot be approved twice (idempotency-key replay and a
  fresh second approval must both be rejected).
"""

from app.models.enums import KycStatus, Role
from tests.conftest import auth_headers, create_user, login


async def _submit_kyc(client, customer_token: str) -> str:
    """Drives a full submission so the profile reaches UNDER_REVIEW — Ops
    can only act on a profile that's actually been submitted, by design
    (NOT_STARTED has no direct transition to APPROVED/REJECTED)."""
    res = await client.post(
        "/api/v1/kyc/submit",
        data={
            "personalInfo": '{"dateOfBirth":"1995-01-01","address":"1 Main St","city":"Pune","state":"MH","pinCode":"411001"}',
            "bankAccount": '{"accountHolderName":"Test User","accountNumber":"123456789012","confirmAccountNumber":"123456789012","ifsc":"HDFC0000001"}',
        },
        files={
            "idProof": ("id.png", b"fake-image-bytes", "image/png"),
            "addressProof": ("address.png", b"fake-image-bytes", "image/png"),
            "panCard": ("pan.png", b"fake-image-bytes", "image/png"),
        },
        headers=auth_headers(customer_token),
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "UNDER_REVIEW"
    return res.json()["id"]


async def test_funding_blocked_until_kyc_approved(client):
    await create_user(
        full_name="Unverified Customer",
        email="kyc-customer1@test.local",
        mobile="9000000020",
        kyc_status=KycStatus.NOT_STARTED,
    )
    await create_user(full_name="Ops", email="kyc-ops1@test.local", mobile="9000000021", role=Role.OPERATIONS)

    customer_token = await login(client, "kyc-customer1@test.local")
    ops_token = await login(client, "kyc-ops1@test.local")

    blocked = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": 5000, "paymentDetails": {"upiId": "c@upi"}},
        headers=auth_headers(customer_token),
    )
    assert blocked.status_code == 403

    profile_id = await _submit_kyc(client, customer_token)

    approve_res = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "kyc-approve-1"},
    )
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json()["status"] == "APPROVED"

    allowed = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": 5000, "paymentDetails": {"upiId": "c@upi"}},
        headers=auth_headers(customer_token),
    )
    assert allowed.status_code == 201, allowed.text


async def test_customer_cannot_access_ops_kyc_endpoints(client):
    await create_user(
        full_name="Customer",
        email="kyc-customer2@test.local",
        mobile="9000000022",
        kyc_status=KycStatus.NOT_STARTED,
    )
    customer_token = await login(client, "kyc-customer2@test.local")

    list_res = await client.get("/api/v1/operations/kyc-profiles", headers=auth_headers(customer_token))
    assert list_res.status_code == 403

    profile_id = await _submit_kyc(client, customer_token)
    approve_res = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/approve",
        headers={**auth_headers(customer_token), "Idempotency-Key": "should-not-work"},
    )
    assert approve_res.status_code == 403


async def test_double_approve_kyc_rejected(client):
    await create_user(
        full_name="Customer",
        email="kyc-customer3@test.local",
        mobile="9000000023",
        kyc_status=KycStatus.NOT_STARTED,
    )
    await create_user(full_name="Ops", email="kyc-ops3@test.local", mobile="9000000024", role=Role.OPERATIONS)

    customer_token = await login(client, "kyc-customer3@test.local")
    ops_token = await login(client, "kyc-ops3@test.local")
    profile_id = await _submit_kyc(client, customer_token)

    first = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-kyc-key-1"},
    )
    assert first.status_code == 200

    # Same idempotency key replayed (e.g. a network retry) — must be rejected.
    replay = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-kyc-key-1"},
    )
    assert replay.status_code == 409

    # A fresh idempotency key (e.g. a second deliberate click) — the state
    # machine itself must still refuse a second approval.
    second_click = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-kyc-key-2"},
    )
    assert second_click.status_code == 409


async def test_rejected_kyc_can_be_resubmitted_for_review(client):
    await create_user(
        full_name="Customer",
        email="kyc-customer4@test.local",
        mobile="9000000025",
        kyc_status=KycStatus.NOT_STARTED,
    )
    await create_user(full_name="Ops", email="kyc-ops4@test.local", mobile="9000000026", role=Role.OPERATIONS)

    customer_token = await login(client, "kyc-customer4@test.local")
    ops_token = await login(client, "kyc-ops4@test.local")
    profile_id = await _submit_kyc(client, customer_token)

    reject_res = await client.post(
        f"/api/v1/operations/kyc-profiles/{profile_id}/reject",
        json={"reason": "ID proof image is unreadable."},
        headers=auth_headers(ops_token),
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"

    still_blocked = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": 5000, "paymentDetails": {"upiId": "c@upi"}},
        headers=auth_headers(customer_token),
    )
    assert still_blocked.status_code == 403

    # Resubmission (REJECTED -> UNDER_REVIEW) must be allowed, unlike
    # resubmitting while already SUBMITTED/UNDER_REVIEW/APPROVED.
    await _submit_kyc(client, customer_token)

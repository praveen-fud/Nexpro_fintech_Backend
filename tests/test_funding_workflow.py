"""CRITICAL TESTS:
- Submitting approval twice must NOT credit the wallet twice.
- A rejected funding request must NOT credit the wallet.
"""

from decimal import Decimal

from app.models.enums import Role
from tests.conftest import bank_payload, auth_headers, create_user, login


async def _create_funding_request(client, customer_token: str, amount: int = 5000) -> str:
    res = await client.post(
        "/api/v1/funding-requests",
        json=bank_payload(amount),
        headers=auth_headers(customer_token),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _open_in_queue(client, ops_token: str, funding_request_id: str) -> None:
    """Mirrors the real UI: Operations always opens a request (PENDING ->
    UNDER_REVIEW) before acting on it — there is no direct PENDING -> APPROVED
    transition, by design."""
    res = await client.get(f"/api/v1/operations/funding-requests/{funding_request_id}", headers=auth_headers(ops_token))
    assert res.status_code == 200, res.text


async def test_double_approval_does_not_credit_wallet_twice(client):
    await create_user(full_name="Customer", email="customer6@test.local", mobile="9000000009")
    await create_user(full_name="Ops", email="ops2@test.local", mobile="9000000010", role=Role.OPERATIONS)

    customer_token = await login(client, "customer6@test.local")
    ops_token = await login(client, "ops2@test.local")

    funding_request_id = await _create_funding_request(client, customer_token, amount=5000)
    await _open_in_queue(client, ops_token, funding_request_id)

    first = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-approve-key-1"},
    )
    assert first.status_code == 200

    # Same idempotency key replayed (e.g. a network retry) — must be rejected.
    replay = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-approve-key-1"},
    )
    assert replay.status_code == 409

    # A fresh idempotency key (e.g. a second deliberate click) — the state
    # machine itself must still refuse a second approval.
    second_click = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "double-approve-key-2"},
    )
    assert second_click.status_code == 409

    wallet = (await client.get("/api/v1/wallet/me", headers=auth_headers(customer_token))).json()
    assert Decimal(wallet["availableBalance"]) == Decimal("5000.00")


async def test_rejected_funding_request_does_not_credit_wallet(client):
    await create_user(full_name="Customer", email="customer7@test.local", mobile="9000000011")
    await create_user(full_name="Ops", email="ops3@test.local", mobile="9000000012", role=Role.OPERATIONS)

    customer_token = await login(client, "customer7@test.local")
    ops_token = await login(client, "ops3@test.local")

    funding_request_id = await _create_funding_request(client, customer_token, amount=5000)
    await _open_in_queue(client, ops_token, funding_request_id)

    reject_res = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/reject",
        json={"reason": "Payment reference could not be verified."},
        headers=auth_headers(ops_token),
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"

    wallet = (await client.get("/api/v1/wallet/me", headers=auth_headers(customer_token))).json()
    assert Decimal(wallet["availableBalance"]) == Decimal("0.00")
    assert Decimal(wallet["pendingBalance"]) == Decimal("0.00")

    # A rejected request can't be approved after the fact either.
    approve_after_reject = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "post-reject-approve"},
    )
    assert approve_after_reject.status_code == 409


async def _approved_customer(client):
    await create_user(full_name="Utr Customer", email="utr-cust@test.local", mobile="9000000077")
    return await login(client, "utr-cust@test.local")


async def test_same_utr_cannot_be_submitted_twice(client):
    token = await _approved_customer(client)
    body = bank_payload(1000)
    first = await client.post("/api/v1/funding-requests", json=body, headers=auth_headers(token))
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/funding-requests", json=body, headers=auth_headers(token))
    assert second.status_code == 409


async def test_utr_is_required_and_validated(client):
    token = await _approved_customer(client)
    for details in ({}, {"referenceNumber": "12"}, {"referenceNumber": "abc def 12345"}):
        res = await client.post(
            "/api/v1/funding-requests",
            json={"method": "BANK_TRANSFER", "amount": 1000, "paymentDetails": details},
            headers=auth_headers(token),
        )
        assert res.status_code == 400, details


async def test_upi_requires_payment_screenshot(client):
    token = await _approved_customer(client)
    res = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": 1000, "paymentDetails": {"referenceNumber": "123456789012"}},
        headers=auth_headers(token),
    )
    assert res.status_code == 400


async def test_upi_payment_link_is_signed_and_expiring(client):
    token = await _approved_customer(client)
    ref = "NXP-00077-AB12"  # mobile 9000000077 -> last 5 = 00077
    res = await client.post(
        "/api/v1/funding-requests/upi/payment-link",
        json={"amount": 2500, "reference": ref},
        headers=auth_headers(token),
    )
    assert res.status_code == 200, res.text
    link = res.json()["token"]

    # Public (no auth) and exposes only payee + amount + note.
    pub = await client.get(f"/api/v1/public/upi-payment/{link}")
    assert pub.status_code == 200
    body = pub.json()
    assert Decimal(str(body["amount"])) == Decimal("2500") and body["reference"] == ref
    assert "customer" not in str(body).lower() and "email" not in body

    # Tampered token and a login access token are both rejected.
    assert (await client.get(f"/api/v1/public/upi-payment/{link[:-3]}abc")).status_code == 404
    assert (await client.get(f"/api/v1/public/upi-payment/{token}")).status_code == 404


async def test_payment_link_rejects_someone_elses_reference(client):
    token = await _approved_customer(client)
    res = await client.post(
        "/api/v1/funding-requests/upi/payment-link",
        json={"amount": 2500, "reference": "NXP-99999-AB12"},
        headers=auth_headers(token),
    )
    assert res.status_code == 400

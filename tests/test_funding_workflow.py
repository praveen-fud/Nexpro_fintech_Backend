"""CRITICAL TESTS:
- Submitting approval twice must NOT credit the wallet twice.
- A rejected funding request must NOT credit the wallet.
"""

from decimal import Decimal

from app.models.enums import Role
from tests.conftest import auth_headers, create_user, login


async def _create_funding_request(client, customer_token: str, amount: int = 5000) -> str:
    res = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": amount, "paymentDetails": {"upiId": "c@upi"}},
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

"""CRITICAL TEST: a user without Operations/Super Admin access must never be
able to approve a funding request, regardless of what they send."""

from app.models.enums import Role
from tests.conftest import bank_payload, auth_headers, create_user, login


async def test_customer_cannot_approve_funding_request(client):
    customer = await create_user(full_name="Customer", email="customer3@test.local", mobile="9000000005")
    customer_token = await login(client, "customer3@test.local")

    create_res = await client.post(
        "/api/v1/funding-requests",
        json=bank_payload(5000),
        headers=auth_headers(customer_token),
    )
    funding_request_id = create_res.json()["id"]

    approve_res = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(customer_token), "Idempotency-Key": "test-key-1"},
    )
    assert approve_res.status_code == 403


async def test_customer_cannot_view_operations_queue(client):
    await create_user(full_name="Customer", email="customer4@test.local", mobile="9000000006")
    customer_token = await login(client, "customer4@test.local")

    res = await client.get("/api/v1/operations/funding-requests", headers=auth_headers(customer_token))
    assert res.status_code == 403


async def test_operations_role_can_approve(client):
    customer = await create_user(full_name="Customer", email="customer5@test.local", mobile="9000000007")
    ops = await create_user(
        full_name="Ops", email="ops1@test.local", mobile="9000000008", role=Role.OPERATIONS
    )

    customer_token = await login(client, "customer5@test.local")
    ops_token = await login(client, "ops1@test.local")

    create_res = await client.post(
        "/api/v1/funding-requests",
        json=bank_payload(5000),
        headers=auth_headers(customer_token),
    )
    funding_request_id = create_res.json()["id"]

    detail_res = await client.get(
        f"/api/v1/operations/funding-requests/{funding_request_id}", headers=auth_headers(ops_token)
    )
    assert detail_res.status_code == 200

    approve_res = await client.post(
        f"/api/v1/operations/funding-requests/{funding_request_id}/approve",
        headers={**auth_headers(ops_token), "Idempotency-Key": "test-key-2"},
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "APPROVED"


async def test_requests_inbox_shows_funding_and_kyc_to_staff_only(client):
    from app.models.enums import KycStatus

    await create_user(full_name="Ops", email="ops-inbox@test.local", mobile="9000000041", role=Role.OPERATIONS)
    await create_user(full_name="Cust", email="cust-inbox@test.local", mobile="9000000042")
    ops = await login(client, "ops-inbox@test.local")
    cust = await login(client, "cust-inbox@test.local")
    await create_user(
        full_name="Pending Kyc", email="pk@test.local", mobile="9000000043", kyc_status=KycStatus.NOT_STARTED
    )

    res = await client.post("/api/v1/funding-requests", json=bank_payload(7000), headers=auth_headers(cust))
    assert res.status_code == 201

    inbox = await client.get("/api/v1/operations/requests", headers=auth_headers(ops))
    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["summary"]["fundingAwaiting"] == 1
    assert body["items"][0]["kind"] == "FUNDING" and body["items"][0]["needsAction"] is True
    assert "No proof" in body["items"][0]["flags"] or body["items"][0]["detail"].startswith("UTR")

    forbidden = await client.get("/api/v1/operations/requests", headers=auth_headers(cust))
    assert forbidden.status_code == 403

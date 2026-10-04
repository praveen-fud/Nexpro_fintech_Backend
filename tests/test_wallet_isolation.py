"""CRITICAL TEST: a customer must never be able to read another customer's
wallet or funding requests."""

from tests.conftest import auth_headers, create_user, login


async def test_customer_cannot_access_another_customers_funding_request(client):
    alice = await create_user(full_name="Alice", email="alice@test.local", mobile="9000000001")
    bob = await create_user(full_name="Bob", email="bob@test.local", mobile="9000000002")

    alice_token = await login(client, "alice@test.local")
    bob_token = await login(client, "bob@test.local")

    create_res = await client.post(
        "/api/v1/funding-requests",
        json={"method": "UPI", "amount": 5000, "paymentDetails": {"upiId": "alice@upi"}},
        headers=auth_headers(alice_token),
    )
    assert create_res.status_code == 201, create_res.text
    funding_request_id = create_res.json()["id"]

    bob_res = await client.get(f"/api/v1/funding-requests/{funding_request_id}", headers=auth_headers(bob_token))
    assert bob_res.status_code == 404

    alice_res = await client.get(f"/api/v1/funding-requests/{funding_request_id}", headers=auth_headers(alice_token))
    assert alice_res.status_code == 200


async def test_customer_wallet_endpoint_only_ever_returns_own_wallet(client):
    alice = await create_user(full_name="Alice", email="alice2@test.local", mobile="9000000003")
    bob = await create_user(full_name="Bob", email="bob2@test.local", mobile="9000000004")

    alice_token = await login(client, "alice2@test.local")
    bob_token = await login(client, "bob2@test.local")

    alice_wallet = (await client.get("/api/v1/wallet/me", headers=auth_headers(alice_token))).json()
    bob_wallet = (await client.get("/api/v1/wallet/me", headers=auth_headers(bob_token))).json()

    assert alice_wallet["id"] != bob_wallet["id"]
    assert alice_wallet["walletId"] != bob_wallet["walletId"]

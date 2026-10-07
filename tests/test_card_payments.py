"""Card payments: the wallet is only ever credited after the gateway itself
confirms a captured payment for the right customer and the right amount."""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.services import razorpay_service
from tests.conftest import auth_headers, create_user, login

SECRET = "test_secret"
WEBHOOK_SECRET = "whsec_test"


@pytest.fixture(autouse=True)
def _razorpay(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(s, "razorpay_key_secret", SECRET)
    monkeypatch.setattr(s, "razorpay_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(razorpay_service, "settings", s)


def _sig(order_id: str, payment_id: str) -> str:
    return hmac.new(SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _fake_gateway(monkeypatch, *, customer_id: str, amount="1000", paise=100000, pay_status="captured"):
    async def fetch_order(order_id):
        return {"id": order_id, "amount": paise, "notes": {"customerId": customer_id, "requestedAmount": amount}}

    async def fetch_payment(payment_id):
        return {"id": payment_id, "order_id": "order_1", "amount": paise, "status": pay_status}

    monkeypatch.setattr(razorpay_service, "fetch_order", fetch_order)
    monkeypatch.setattr(razorpay_service, "fetch_payment", fetch_payment)


async def _customer(client, email="card@test.local", mobile="9000000088"):
    user = await create_user(full_name="Card Customer", email=email, mobile=mobile)
    return user, await login(client, email)


async def _wallet_balance(client, token) -> Decimal:
    res = await client.get("/api/v1/wallet/me", headers=auth_headers(token))
    assert res.status_code == 200, res.text
    return Decimal(str(res.json()["availableBalance"]))


async def test_forged_signature_is_rejected(client, monkeypatch):
    user, token = await _customer(client)
    _fake_gateway(monkeypatch, customer_id=str(user.id))
    res = await client.post(
        "/api/v1/funding-requests/card/verify",
        json={"orderId": "order_1", "paymentId": "pay_1", "signature": "0" * 64},
        headers=auth_headers(token),
    )
    assert res.status_code == 400
    assert await _wallet_balance(client, token) == 0


async def test_verified_payment_credits_wallet_once(client, monkeypatch):
    user, token = await _customer(client)
    _fake_gateway(monkeypatch, customer_id=str(user.id))
    body = {"orderId": "order_1", "paymentId": "pay_1", "signature": _sig("order_1", "pay_1")}

    first = await client.post("/api/v1/funding-requests/card/verify", json=body, headers=auth_headers(token))
    assert first.status_code == 200, first.text
    second = await client.post("/api/v1/funding-requests/card/verify", json=body, headers=auth_headers(token))
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert await _wallet_balance(client, token) == Decimal("1000")


async def test_cannot_claim_another_customers_payment(client, monkeypatch):
    victim, _ = await _customer(client, "victim@test.local", "9000000091")
    _, attacker_token = await _customer(client, "attacker@test.local", "9000000092")
    _fake_gateway(monkeypatch, customer_id=str(victim.id))
    res = await client.post(
        "/api/v1/funding-requests/card/verify",
        json={"orderId": "order_1", "paymentId": "pay_1", "signature": _sig("order_1", "pay_1")},
        headers=auth_headers(attacker_token),
    )
    assert res.status_code == 404
    assert await _wallet_balance(client, attacker_token) == 0


async def test_uncaptured_payment_does_not_credit(client, monkeypatch):
    user, token = await _customer(client)
    _fake_gateway(monkeypatch, customer_id=str(user.id), pay_status="authorized")
    res = await client.post(
        "/api/v1/funding-requests/card/verify",
        json={"orderId": "order_1", "paymentId": "pay_1", "signature": _sig("order_1", "pay_1")},
        headers=auth_headers(token),
    )
    assert res.status_code == 409
    assert await _wallet_balance(client, token) == 0


async def test_tampered_amount_is_refused(client, monkeypatch):
    user, token = await _customer(client)
    # Gateway says only ₹10 was charged for a claimed ₹1000 order.
    _fake_gateway(monkeypatch, customer_id=str(user.id), amount="1000", paise=1000)
    res = await client.post(
        "/api/v1/funding-requests/card/verify",
        json={"orderId": "order_1", "paymentId": "pay_1", "signature": _sig("order_1", "pay_1")},
        headers=auth_headers(token),
    )
    assert res.status_code == 400
    assert await _wallet_balance(client, token) == 0


async def test_webhook_requires_valid_signature_and_is_idempotent(client, monkeypatch):
    user, token = await _customer(client)
    _fake_gateway(monkeypatch, customer_id=str(user.id))
    event = json.dumps(
        {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1"}}}}
    ).encode()

    bad = await client.post("/api/v1/funding-requests/card/webhook", content=event, headers={"x-razorpay-signature": "x"})
    assert bad.status_code == 400

    good_sig = hmac.new(WEBHOOK_SECRET.encode(), event, hashlib.sha256).hexdigest()
    for _ in range(2):
        ok = await client.post(
            "/api/v1/funding-requests/card/webhook", content=event, headers={"x-razorpay-signature": good_sig}
        )
        assert ok.status_code == 200
    assert await _wallet_balance(client, token) == Decimal("1000")

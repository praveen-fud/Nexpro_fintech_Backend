from decimal import Decimal

from app.models.enums import KycStatus
from app.services import card_to_bank_service as pricing
from tests.conftest import auth_headers, create_user, login


def test_recipient_always_gets_full_amount_and_fees_add_up():
    q = pricing.calculate(Decimal("10000"))
    assert q["amount_to_bank"] == Decimal("10000.00")
    assert q["gateway_charge"] == Decimal("200.00")
    assert q["commission"] == Decimal("100.00")
    assert q["gst"] == Decimal("54.90")  # 18% of (200 + 100 + 5)
    assert q["total_fees"] == Decimal("359.90")
    assert q["card_total"] == Decimal("10359.90")
    assert q["card_total"] == q["amount_to_bank"] + q["gateway_charge"] + q["commission"] + q["payout_fee"] + q["gst"]


def test_minimum_commission_applies_on_small_amounts():
    assert pricing.calculate(Decimal("1000"))["commission"] == pricing.MIN_COMMISSION


async def test_quote_endpoint_enforces_limits_and_kyc(client):
    await create_user(full_name="C2B", email="c2b@test.local", mobile="9000000066")
    token = await login(client, "c2b@test.local")
    ok = await client.get("/api/v1/card-to-bank/quote", params={"amount": 5000}, headers=auth_headers(token))
    assert ok.status_code == 200 and Decimal(str(ok.json()["amountToBank"])) == Decimal("5000")
    for bad in (999, 100001):
        res = await client.get("/api/v1/card-to-bank/quote", params={"amount": bad}, headers=auth_headers(token))
        assert res.status_code == 400

    await create_user(
        full_name="No KYC", email="nokyc@test.local", mobile="9000000067", kyc_status=KycStatus.NOT_STARTED
    )
    token2 = await login(client, "nokyc@test.local")
    res = await client.get("/api/v1/card-to-bank/quote", params={"amount": 5000}, headers=auth_headers(token2))
    assert res.status_code == 403

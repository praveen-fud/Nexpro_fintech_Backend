"""Razorpay Standard Checkout integration (https://razorpay.com/docs/payments/).

Card data never reaches this server: the customer enters it inside Razorpay's
hosted checkout. We only create an order, then verify — server side, with the
secret key — that the order was really paid before crediting anything.
"""

import hashlib
import hmac
from decimal import Decimal
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings

settings = get_settings()

_API = "https://api.razorpay.com/v1"
_TIMEOUT = httpx.Timeout(15.0)


def is_configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def _auth() -> tuple[str, str]:
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Card payments are not available right now."
        )
    return settings.razorpay_key_id, settings.razorpay_key_secret


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, auth=_auth()) as client:
            res = await client.request(method, f"{_API}{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not reach the payment provider. Try again."
        ) from exc
    if res.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="The payment provider rejected the request."
        )
    return res.json()


def to_paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


async def create_order(*, amount_paise: int, receipt: str, notes: dict[str, str]) -> dict[str, Any]:
    return await _request(
        "POST", "/orders", json={"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes}
    )


async def fetch_order(order_id: str) -> dict[str, Any]:
    return await _request("GET", f"/orders/{order_id}")


async def fetch_payment(payment_id: str) -> dict[str, Any]:
    return await _request("GET", f"/payments/{payment_id}")


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Signature Razorpay returns to the browser: HMAC_SHA256(order_id|payment_id, key_secret)."""
    if not settings.razorpay_key_secret:
        return False
    expected = _hmac_hex(settings.razorpay_key_secret, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    if not settings.razorpay_webhook_secret:
        return False
    return hmac.compare_digest(_hmac_hex(settings.razorpay_webhook_secret, body), signature)

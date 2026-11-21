"""
Sanitizer for the client-supplied ``paymentDetails`` blob on a funding
request.

The blob used to be persisted verbatim into PaymentAttempt.details. Nothing
in the current frontend puts card data there — the credit-card flow hands
card entry to the provider's hosted checkout and never posts a PAN to us —
but "the current frontend doesn't" is not a control. The API accepts
whatever a caller sends, so this allowlists the handful of fields the flows
actually use, bounds them, and scrubs anything that looks like a card
number or CVV before it can reach the database.

Keeping raw PANs out of our storage is the difference between "out of PCI
DSS scope" and "in scope for the whole standard".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# The only keys any funding flow legitimately sends.
_ALLOWED_KEYS = frozenset(
    {
        "reference",         # bank-transfer reference the customer quotes
        "referenceNumber",   # UPI / bank-transfer UTR
        "upiId",             # payer VPA
        "maskedCard",        # last-4 only, e.g. "XXXXXXXXXXXX4242"
        "cardNetwork",       # e.g. "Visa"
        "bankName",
        "transferDate",
        "transactionDate",
        "gatewayOrderId",    # Razorpay order id
        "proofFileName",     # display name only; the path lives in its own column
        "note",              # optional free-text note
    }
)

_MAX_VALUE_CHARS = 128
_MAX_KEYS = 12

# Keys whose names alone mean the client is trying to send something we must
# never accept, whatever the value looks like.
_FORBIDDEN_KEY_HINTS = (
    "cardnumber",
    "card_no",
    "cardno",
    "pan",
    "cvv",
    "cvc",
    "expiry",
    "expirymonth",
    "expiryyear",
    "cardholder",
    "password",
    "otp",
    "pin",
    "token",
    "secret",
)

# 13-19 digits, optionally separated by spaces or hyphens: a card number.
_PAN_CANDIDATE = re.compile(r"(?:\d[ -]?){13,19}")
_MASKED_TAIL_ONLY = re.compile(r"^[X*x•\s-]{4,}\d{4}$")


def _luhn_valid(digits: str) -> bool:
    """Luhn check, so we only treat a digit run as a PAN when it plausibly
    is one — a 16-digit order reference should not be redacted."""
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = ord(char) - 48
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _scrub_pan(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return f"[redacted-pan:{digits[-4:]}]"
        return raw

    return _PAN_CANDIDATE.sub(replace, value)


def _clean_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        # Nested objects and arrays have no legitimate use here.
        return None

    text = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    text = " ".join(text.split())[:_MAX_VALUE_CHARS]
    return _scrub_pan(text)


def sanitize_payment_details(raw: Any) -> dict[str, Any]:
    """Return a bounded, allowlisted, PAN-free copy of ``raw``.

    Unknown keys are dropped rather than rejected: a client sending an extra
    field should not fail its funding request, but that field has no reason
    to be stored either.
    """
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key, value in list(raw.items())[: _MAX_KEYS * 4]:
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        flat_key = normalized_key.replace("-", "").replace("_", "").lower()

        if any(hint in flat_key for hint in _FORBIDDEN_KEY_HINTS):
            # Deliberately not echoed back or stored under any name.
            continue
        if normalized_key not in _ALLOWED_KEYS:
            continue

        scalar = _clean_scalar(value)
        if scalar is None or scalar == "":
            continue

        if normalized_key == "maskedCard" and isinstance(scalar, str) and not _MASKED_TAIL_ONLY.match(scalar):
            # A "masked" card that is not actually masked is a PAN.
            continue

        cleaned[normalized_key] = scalar
        if len(cleaned) >= _MAX_KEYS:
            break

    return cleaned

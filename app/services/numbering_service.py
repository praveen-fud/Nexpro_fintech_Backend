import secrets
from datetime import UTC, datetime


def _stamped(prefix: str) -> str:
    # Date-stamped + random suffix — human-readable without exposing a
    # sequential database id (per the project's security requirements).
    return f"{prefix}-{datetime.now(UTC):%y%m%d}-{secrets.token_hex(3).upper()}"


def new_request_number() -> str:
    return _stamped("FR")


def new_transaction_number() -> str:
    return _stamped("TXN")


def new_customer_reference(mobile_number: str) -> str:
    return f"NXP-{mobile_number[-5:]}-{secrets.token_hex(2).upper()}"

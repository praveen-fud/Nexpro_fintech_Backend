from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _finite_money(value: object) -> object:
    """Reject non-finite decimals before any comparison touches them.

    ``Decimal("NaN")`` and the infinities are accepted by Pydantic's Decimal
    parsing, but comparing them against a bound raises InvalidOperation — so
    a field declared ``Decimal = Field(gt=0)`` turns ``{"amount": "NaN"}``
    into an unhandled 500 instead of a validation error. Anything money-
    shaped therefore goes through here first.
    """
    if isinstance(value, float):
        # Route floats through str so 0.1 does not become 0.1000000000000000055.
        value = str(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise ValueError("Enter an amount")
        try:
            value = Decimal(candidate)
        except ArithmeticError as exc:
            raise ValueError("Enter a valid amount") from exc
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Enter a valid amount")
    return value


# A money value safe to compare and to store in Numeric(18, 2): finite, and
# no more precise than paise.
MoneyAmount = Annotated[Decimal, BeforeValidator(_finite_money)]


def to_camel_case(snake: str) -> str:
    first, *rest = snake.split("_")
    return first + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    """Base schema: Python stays snake_case, JSON over the wire is camelCase
    to match the TypeScript frontend's domain types exactly."""

    model_config = ConfigDict(alias_generator=to_camel_case, populate_by_name=True, from_attributes=True)


class PaginatedResponse(CamelModel):
    items: list
    total: int
    page: int
    page_size: int

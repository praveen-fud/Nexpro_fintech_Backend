"""Credit-card → bank-transfer service pricing.

STATIC rate card for now (one place to change). The customer asks for an exact
amount to land in a bank account; every charge on top is added to what their card
is billed, so the recipient always receives the full amount.

    gateway charge   = amount × GATEWAY_RATE      (card network / gateway MDR)
    commission       = amount × COMMISSION_RATE   (Nexpro's margin)
    payout fee       = flat per transfer          (IMPS rail cost)
    GST              = 18% on gateway + commission + payout fee
    card total       = amount + all of the above

The frontend never computes any of this; it only renders what /quote returns.
"""

from decimal import ROUND_HALF_UP, Decimal

GATEWAY_RATE = Decimal("2.00")       # % of amount
COMMISSION_RATE = Decimal("1.00")    # % of amount
MIN_COMMISSION = Decimal("10.00")    # ₹ — commission never below this
PAYOUT_FEE = Decimal("5.00")         # ₹ flat per transfer
GST_RATE = Decimal("18.00")          # % on all of the above
MIN_AMOUNT = Decimal("1000")
MAX_AMOUNT = Decimal("100000")       # per transfer
PAYOUT_ETA = "Usually within 30 minutes via IMPS (up to 2 hours on bank holidays)"


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate(amount: Decimal) -> dict[str, Decimal]:
    gateway = _money(amount * GATEWAY_RATE / 100)
    commission = max(_money(amount * COMMISSION_RATE / 100), MIN_COMMISSION)
    payout = PAYOUT_FEE
    gst = _money((gateway + commission + payout) * GST_RATE / 100)
    total_fees = gateway + commission + payout + gst
    return {
        "amount_to_bank": _money(amount),
        "gateway_charge": gateway,
        "commission": commission,
        "payout_fee": payout,
        "gst": gst,
        "total_fees": total_fees,
        "card_total": _money(amount) + total_fees,
    }

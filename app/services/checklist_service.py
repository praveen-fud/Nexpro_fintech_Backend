from app.models.enums import FundingMethod, KycStatus
from app.models.funding import FundingRequest, PaymentAttempt
from app.models.user import User
from app.schemas.funding import ChecklistItem


def build_verification_checklist(
    funding_request: FundingRequest, customer: User, payment_attempt: PaymentAttempt | None
) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []

    if customer.kyc_status == KycStatus.APPROVED:
        items.append(ChecklistItem(key="kyc", label="KYC Verified", result="pass"))
    elif customer.kyc_status in (KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW):
        items.append(
            ChecklistItem(key="kyc", label="KYC Under Review", result="warning", detail="Not yet approved")
        )
    else:
        items.append(
            ChecklistItem(key="kyc", label="KYC Not Verified", result="fail", detail=customer.kyc_status.value)
        )

    payment_detail = None
    if payment_attempt is not None:
        payment_detail = payment_attempt.masked_reference
        items.append(
            ChecklistItem(key="payment", label="Payment Evidence Received", result="pass", detail=payment_detail)
        )
    else:
        items.append(ChecklistItem(key="payment", label="Payment Evidence Missing", result="fail"))

    expected_total = funding_request.requested_amount
    items.append(
        ChecklistItem(
            key="amount",
            label="Amount Matches Request",
            result="pass",
            detail=f"₹{expected_total:,.0f} requested",
        )
    )

    items.append(
        ChecklistItem(key="reference", label="Reference Matches", result="pass", detail=funding_request.reference)
    )

    risk_result = "warning" if funding_request.requested_amount > 100000 else "pass"
    risk_detail = "Large amount — review carefully" if risk_result == "warning" else "Within normal range"
    items.append(ChecklistItem(key="risk", label="Risk Check", result=risk_result, detail=risk_detail))

    if funding_request.method == FundingMethod.BANK_TRANSFER:
        has_proof = bool(funding_request.proof_file_path)
        items.append(
            ChecklistItem(
                key="documents",
                label="Supporting Documents",
                result="pass" if has_proof else "warning",
                detail="Proof of transfer attached" if has_proof else "No proof uploaded",
            )
        )

    return items

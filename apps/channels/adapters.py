"""
Per-channel webhook adapters: validate() / normalise() / verify_signature(). Real
bank integration is simulated/illustrative (PRD.md §5) — these adapters define the
payload contract every channel is expected to honour once real integrations land.
See TDD.md §4.6 and API_REFERENCE.md 'e-Channel integration'.
"""
import hashlib
import hmac
from decimal import Decimal

from apps.payments.models import PaymentChannel

REQUIRED_FIELDS = {
    PaymentChannel.POS: ["terminalId", "rrn", "amount", "billRef"],
    PaymentChannel.OTC: ["tellerRef", "branchCode", "amount", "billRef"],
    PaymentChannel.IB_MB: ["sessionId", "amount", "billRef"],
    PaymentChannel.USSD: ["ussdRef", "msisdn", "amount", "billRef"],
    PaymentChannel.FIRSTMONIE: ["agentTxnRef", "agentId", "amount", "billRef"],
}

BANK_REF_FIELD = {
    PaymentChannel.POS: "rrn",
    PaymentChannel.OTC: "tellerRef",
    PaymentChannel.IB_MB: "sessionId",
    PaymentChannel.USSD: "ussdRef",
    PaymentChannel.FIRSTMONIE: "agentTxnRef",
}


class AdapterError(Exception):
    pass


def validate(code: str, payload: dict) -> None:
    required = REQUIRED_FIELDS.get(code)
    if required is None:
        raise AdapterError(f"Unknown channel code: {code}")
    missing = [f for f in required if f not in payload]
    if missing:
        raise AdapterError(f"Missing required field(s): {', '.join(missing)}")


def normalise(code: str, payload: dict) -> dict:
    amount = Decimal(str(payload["amount"]))
    if payload.get("amountInKobo"):
        amount = amount / 100
    return {
        "bank_txn_ref": str(payload[BANK_REF_FIELD[code]]),
        "amount": amount,
        "bill_ref": payload["billRef"],
    }


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

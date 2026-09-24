"""
Per-channel webhook adapters: validate() / normalise() / verify_signature(). Real
bank integration is simulated/illustrative (PRD.md §5) — these adapters define the
payload contract every channel is expected to honour once real integrations land.
See TDD.md §4.6 and API_REFERENCE.md 'e-Channel integration'.
"""
import hashlib
import hmac
from decimal import Decimal, InvalidOperation

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


#: DecimalField(max_digits=14, decimal_places=2) on Payment/ChannelTransactionFeed.
_MAX_AMOUNT = Decimal("999999999999.99")


def _parse_amount(value) -> Decimal:
    """Turns whatever a caller put in ``amount`` into a usable Decimal or raises
    AdapterError — never anything else. This runs on unauthenticated input
    (before the signature check can even be attempted, because the signature
    lookup itself depends on the billRef), so "abc", NaN, Infinity, a list or a
    number too large for the column must all be a clean 400, not a 500."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise AdapterError("amount must be a number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise AdapterError("amount must be a number") from None
    if not amount.is_finite() or amount <= 0:
        raise AdapterError("amount must be a positive number")
    return amount


def validate(code: str, payload: dict) -> None:
    required = REQUIRED_FIELDS.get(code)
    if required is None:
        raise AdapterError(f"Unknown channel code: {code}")
    if not isinstance(payload, dict):
        raise AdapterError("Payload must be a JSON object")
    missing = [f for f in required if f not in payload]
    if missing:
        raise AdapterError(f"Missing required field(s): {', '.join(missing)}")
    if not isinstance(payload["billRef"], str) or not payload["billRef"].strip():
        raise AdapterError("billRef must be a non-empty string")
    if not isinstance(payload[BANK_REF_FIELD[code]], (str, int)) or isinstance(payload[BANK_REF_FIELD[code]], bool):
        raise AdapterError(f"{BANK_REF_FIELD[code]} must be a string or integer")
    amount = _parse_amount(payload["amount"])
    if payload.get("amountInKobo"):
        amount = amount / 100
    if amount > _MAX_AMOUNT:
        raise AdapterError("amount is too large")


def normalise(code: str, payload: dict) -> dict:
    amount = _parse_amount(payload["amount"])
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

"""
Receipt delivery via email (Resend) and SMS (Termii) — see docs/CHANGELOG.md's
"Tier 3: receipt delivery via email/SMS" entry for the ask. Both are plain
synchronous HTTP calls made from inside the request/response cycle, not a
Celery task: this backend's only deployed service on Render is the web
process — no worker, no Redis — so anything queued through Celery there
would just never run. See CELERY_BROKER_URL's dev-only default in
config/settings/base.py.

Each channel degrades independently and never raises: a missing API key, a
missing payer contact field, or a failed HTTP call all come back as a
structured per-channel result instead of a 500, since the two channels are
inherently unreliable in different ways (no email on file vs. Termii being
down) and one failing shouldn't stop the other from being attempted.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _normalize_ng_phone(phone: str) -> str | None:
    """'0803...' -> '234803...'; already-international numbers pass through."""
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return None
    if digits.startswith("0"):
        return "234" + digits[1:]
    return digits


def _receipt_email_html(receipt) -> str:
    payment = receipt.payment
    bill = payment.bill
    lines = bill.lines.select_related("assessment__council_revenue_item")
    rows = "".join(
        f"<tr><td style='padding:4px 0'>{line.assessment.council_revenue_item.item_name}</td>"
        f"<td style='padding:4px 0;text-align:right'>₦{line.line_amount:,.2f}</td></tr>"
        for line in lines
    )
    return (
        "<div style='font-family:sans-serif;max-width:480px'>"
        f"<h2 style='margin-bottom:4px'>Receipt {receipt.receipt_ref}</h2>"
        f"<p style='margin:4px 0'>Amount received: <strong>₦{payment.amount:,.2f}</strong></p>"
        f"<p style='margin:4px 0'>Bill: {bill.bill_ref}</p>"
        f"<p style='margin:4px 0'>Channel: {payment.channel.code}</p>"
        f"<p style='margin:4px 0'>Date: {payment.created_at:%d %b %Y, %I:%M %p}</p>"
        "<table style='width:100%;border-collapse:collapse;margin-top:12px'>"
        "<thead><tr><th style='text-align:left;border-bottom:1px solid #ccc'>Paid for</th>"
        "<th style='text-align:right;border-bottom:1px solid #ccc'>Amount</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def send_receipt_email(receipt, to_email: str) -> dict:
    if not settings.RESEND_API_KEY:
        return {"attempted": False, "reason": "RESEND_API_KEY not configured"}
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={
                "from": settings.RESEND_FROM_EMAIL,
                "to": [to_email],
                "subject": f"Receipt {receipt.receipt_ref}",
                "html": _receipt_email_html(receipt),
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning("Resend send failed for receipt %s: %s %s", receipt.receipt_ref, resp.status_code, resp.text)
            return {"attempted": True, "sent": False, "error": resp.text[:300]}
        return {"attempted": True, "sent": True}
    except requests.RequestException as exc:
        logger.warning("Resend send raised for receipt %s: %s", receipt.receipt_ref, exc)
        return {"attempted": True, "sent": False, "error": str(exc)}


def send_receipt_sms(receipt, to_phone: str) -> dict:
    if not settings.TERMII_API_KEY:
        return {"attempted": False, "reason": "TERMII_API_KEY not configured"}
    phone = _normalize_ng_phone(to_phone)
    if not phone:
        return {"attempted": False, "reason": "invalid phone number"}

    payment = receipt.payment
    message = (
        f"ACRev360: Receipt {receipt.receipt_ref} confirmed for NGN {payment.amount:,.2f} "
        f"against bill {payment.bill.bill_ref}. Thank you."
    )
    try:
        resp = requests.post(
            "https://api.ns.termii.com/api/sms/send",
            json={
                "to": phone,
                "from": settings.TERMII_SENDER_ID,
                "sms": message,
                "type": "plain",
                "channel": "generic",
                "api_key": settings.TERMII_API_KEY,
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning("Termii send failed for receipt %s: %s %s", receipt.receipt_ref, resp.status_code, resp.text)
            return {"attempted": True, "sent": False, "error": resp.text[:300]}
        return {"attempted": True, "sent": True}
    except requests.RequestException as exc:
        logger.warning("Termii send raised for receipt %s: %s", receipt.receipt_ref, exc)
        return {"attempted": True, "sent": False, "error": str(exc)}


def send_receipt(receipt) -> dict:
    """Sends to whichever contact fields the payer actually has on file —
    email via Resend if payer.email is set, SMS via Termii if payer.phone is
    set. Returns a result for both keys regardless, so the caller can show
    e.g. "no email on file" rather than silently doing nothing."""
    payer = receipt.payment.bill.payer
    return {
        "email": send_receipt_email(receipt, payer.email) if payer.email else {"attempted": False, "reason": "no email on file"},
        "sms": send_receipt_sms(receipt, payer.phone) if payer.phone else {"attempted": False, "reason": "no phone on file"},
    }

"""Resolves which APIClient (if any) authenticates an inbound channel webhook
request. Not a DRF authentication class — WebhookView is intentionally
AllowAny at the transport level (any bank/gateway can call it) and the real
gate is per-request HMAC signature verification against a pool of candidate
clients, not a single bearer credential DRF's authenticate() contract expects.
"""
from django.db import models
from django.utils import timezone

from apps.channels import adapters
from apps.payments.crypto import decrypt_secret
from apps.payments.models import APIClient


class WebhookAuthError(Exception):
    pass


def authenticate_webhook_client(
    *, council, channel, raw_body: bytes, signature_header: str | None,
    required_scope: str = APIClient.SCOPE_WEBHOOK_POST,
) -> APIClient:
    """Finds the active, unexpired, correctly-scoped APIClient whose secret
    produced a valid HMAC signature for this request. Inactive, expired, and
    under-scoped keys are rejected the same way as a bad signature — a caller
    can't distinguish which failure mode applies. Updates last_used_at on the
    matched client."""
    now = timezone.now()
    candidates = APIClient.objects.filter(council=council, channel=channel, is_active=True).filter(
        models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
    )

    for client in candidates:
        if not client.has_scope(required_scope):
            continue
        if adapters.verify_signature(decrypt_secret(client.secret_encrypted), raw_body, signature_header):
            APIClient.objects.filter(pk=client.pk).update(last_used_at=now)
            return client

    raise WebhookAuthError("Signature verification failed")

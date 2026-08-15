import uuid

from django.db import models

from apps.tenancy.models import CouncilScopedModel, WardZone


class PaymentChannel(models.Model):
    """The five channels — council-agnostic catalogue."""

    POS, OTC, IB_MB, USSD, FIRSTMONIE = "POS", "OTC", "IB_MB", "USSD", "FIRSTMONIE"
    CODE_CHOICES = [
        (POS, "POS"),
        (OTC, "Over-the-counter teller"),
        (IB_MB, "Internet / Mobile Banking"),
        (USSD, "USSD"),
        (FIRSTMONIE, "FirstMonie Agent Banking"),
    ]

    code = models.CharField(max_length=16, choices=CODE_CHOICES, unique=True)
    provider = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "payment_channel"

    def __str__(self):
        return self.code


class POSTerminal(CouncilScopedModel):
    ACTIVE, FAULTY, RETIRED = "ACTIVE", "FAULTY", "RETIRED"
    STATUS_CHOICES = [(ACTIVE, "Active"), (FAULTY, "Faulty"), (RETIRED, "Retired")]

    terminal_id = models.CharField(max_length=32)
    agent = models.ForeignKey("accounts.FieldAgent", on_delete=models.PROTECT, related_name="terminals")
    ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, related_name="terminals")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)

    class Meta:
        db_table = "pos_terminal"
        constraints = [
            models.UniqueConstraint(fields=["council", "terminal_id"], name="uniq_terminal_id_per_council"),
        ]

    def __str__(self):
        return self.terminal_id


class APIClient(CouncilScopedModel):
    """Registered API credentials per channel integration, for HMAC signature
    verification on inbound webhooks — see V2_ARCHITECTURE.md §8 (on by default).

    `secret_encrypted` holds the shared secret under reversible (Fernet) encryption,
    not a one-way hash — HMAC verification requires recovering the actual secret to
    recompute the signature server-side, which a hash can never allow. See
    apps/payments/crypto.py."""

    channel = models.ForeignKey(PaymentChannel, on_delete=models.PROTECT, related_name="api_clients")
    api_key = models.CharField(max_length=64, unique=True)
    secret_encrypted = models.CharField(max_length=256)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "api_client"

    def __str__(self):
        return f"{self.channel_id}:{self.api_key}"


class Payment(CouncilScopedModel):
    """The actual money-in event. Every channel's webhook/settlement/manual entry
    ends up as one of these via payments.services.post_payment() — the single
    money-in path, see V2_ARCHITECTURE.md §7.1."""

    PENDING, CONFIRMED, FAILED, REVERSED = "PENDING", "CONFIRMED", "FAILED", "REVERSED"
    TXN_STATUS_CHOICES = [
        (PENDING, "Pending"),
        (CONFIRMED, "Confirmed"),
        (FAILED, "Failed"),
        (REVERSED, "Reversed"),
    ]

    payment_ref = models.CharField(max_length=64, unique=True, blank=True)
    bill = models.ForeignKey("billing.Bill", on_delete=models.PROTECT, related_name="payments")
    channel = models.ForeignKey(PaymentChannel, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    bank_txn_ref = models.CharField(max_length=64, blank=True)
    txn_status = models.CharField(max_length=16, choices=TXN_STATUS_CHOICES, default=CONFIRMED)
    posted_by = models.ForeignKey(
        "accounts.AppUser", on_delete=models.PROTECT, null=True, blank=True, related_name="payments_posted"
    )
    geo_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = "payment"
        indexes = [models.Index(fields=["council", "bill"])]

    def __str__(self):
        return self.payment_ref or f"(unsaved payment #{self.pk})"


class ChannelTransactionFeed(CouncilScopedModel):
    """The bank's side of the story: raw inbound notifications before they're
    matched to a Payment. UNIQUE(channel, bank_txn_ref) is what makes webhook
    replays idempotent."""

    UNMATCHED, MATCHED, EXCEPTION = "UNMATCHED", "MATCHED", "EXCEPTION"
    MATCH_STATUS_CHOICES = [
        (UNMATCHED, "Unmatched"),
        (MATCHED, "Matched"),
        (EXCEPTION, "Exception"),
    ]

    channel = models.ForeignKey(PaymentChannel, on_delete=models.PROTECT, related_name="feed_rows")
    bank_txn_ref = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    raw_payload = models.JSONField(default=dict, blank=True)
    match_status = models.CharField(max_length=16, choices=MATCH_STATUS_CHOICES, default=UNMATCHED)
    matched_payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="feed_matches"
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_transaction_feed"
        constraints = [
            models.UniqueConstraint(fields=["channel", "bank_txn_ref"], name="uniq_channel_bank_txn_ref"),
        ]

    def __str__(self):
        return f"{self.channel_id}:{self.bank_txn_ref}"


class Receipt(CouncilScopedModel):
    """One per Payment, carrying a qr_token for public verification."""

    receipt_ref = models.CharField(max_length=64, unique=True, blank=True)
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="receipt")
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    verified_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "receipt"

    def __str__(self):
        return self.receipt_ref or f"(unsaved receipt #{self.pk})"

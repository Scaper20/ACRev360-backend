import uuid

from django.db import models
from django.utils import timezone

from apps.tenancy.models import CouncilScopedModel, WardZone


class PaymentChannel(models.Model):
    """The six channels — council-agnostic catalogue."""

    POS, OTC, IB_MB, USSD, FIRSTMONIE, CASH = "POS", "OTC", "IB_MB", "USSD", "FIRSTMONIE", "CASH"
    CODE_CHOICES = [
        (POS, "POS"),
        (OTC, "Over-the-counter teller"),
        (IB_MB, "Internet / Mobile Banking"),
        (USSD, "USSD"),
        (FIRSTMONIE, "FirstMonie Agent Banking"),
        (CASH, "Cash"),
    ]

    #: Channels with no bank-side feed to reconcile against by definition —
    #: excluded from unmatched-credit exception logic entirely.
    NO_FEED_EXPECTED = {CASH}

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
    bank_terminal_id = models.CharField(max_length=32, blank=True)
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


#: The only action an API key can currently invoke is authenticating an
#: inbound channel webhook — extend as more actions are opened up to API-key
#: callers. Kept at module level so it can back the scopes field's default
#: (existing rows/tests that never set scopes explicitly keep working).
API_CLIENT_SCOPE_WEBHOOK_POST = "payments.webhook.post"


def _default_api_client_scopes():
    return [API_CLIENT_SCOPE_WEBHOOK_POST]


class APIClient(CouncilScopedModel):
    """Registered API credentials per channel integration, for HMAC signature
    verification on inbound webhooks — see V2_ARCHITECTURE.md §8 (on by default).

    `secret_encrypted` holds the shared secret under reversible (Fernet) encryption,
    not a one-way hash — HMAC verification requires recovering the actual secret to
    recompute the signature server-side, which a hash can never allow. See
    apps/payments/crypto.py."""

    SCOPE_WEBHOOK_POST = API_CLIENT_SCOPE_WEBHOOK_POST
    SCOPE_CHOICES = [SCOPE_WEBHOOK_POST]

    #: Human-chosen label, e.g. "FirstBank Production" — optional, blank for
    #: keys created before this field existed. Never falls back to api_key
    #: itself as a stored value; a blank name is exactly what it means, and
    #: any "show something sane" fallback (channel label, etc.) belongs at
    #: the display layer, not baked into the stored data.
    name = models.CharField(max_length=100, blank=True)
    channel = models.ForeignKey(PaymentChannel, on_delete=models.PROTECT, related_name="api_clients")
    api_key = models.CharField(max_length=64, unique=True)
    secret_encrypted = models.CharField(max_length=256)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Null means the key never expires.")
    scopes = models.JSONField(
        default=_default_api_client_scopes, blank=True, help_text="Action codes this key may invoke — see SCOPE_CHOICES."
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_client"

    def __str__(self):
        return f"{self.channel_id}:{self.api_key}"

    def has_scope(self, code: str) -> bool:
        return code in self.scopes

    def is_expired(self, *, at=None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (at or timezone.now())


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
    terminal = models.ForeignKey(
        POSTerminal, on_delete=models.PROTECT, null=True, blank=True, related_name="payments"
    )
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
        indexes = [
            models.Index(fields=["council", "bill"]),
            # List/dashboard/date-filter path orders and windows by -created_at
            # after a council filter (PERF-2) — lands the common payment scan
            # on one b-tree instead of a council join then a sort.
            models.Index(fields=["council", "created_at"]),
            # Money-path sums (settlements, reconciliation, dashboard) filter
            # council + txn_status=CONFIRMED before aggregating.
            models.Index(fields=["council", "txn_status"]),
        ]
        constraints = [
            # One bank reference can fund one payment per channel. Blank refs
            # (manual cash/teller entries with no bank-side number) are exempt
            # — the feed's own UNIQUE(channel, bank_txn_ref) is unconditional,
            # but a Payment that isn't backed by a feed row has nothing to be
            # idempotent against. This is the backstop that stops a second
            # manual confirmation of the same slip/till reference double-
            # charging a payer (post_payment's bill lock serializes the race,
            # this catches the honest duplicate).
            models.UniqueConstraint(
                fields=["channel", "bank_txn_ref"],
                condition=~models.Q(bank_txn_ref=""),
                name="uniq_channel_bank_txn_ref_nonblank",
            ),
        ]

    def __str__(self):
        return self.payment_ref or f"(unsaved payment #{self.pk})"


class PaymentAllocation(models.Model):
    """How one payment's amount was split across bill lines — FIFO, oldest
    line first, see payments.services.post_payment. PROTECT on both FKs:
    a payment's allocation history must survive exactly as long as the
    payment and the line it was applied against."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    bill_line = models.ForeignKey("billing.BillLine", on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payment_allocation"

    def __str__(self):
        return f"{self.payment_id}->{self.bill_line_id}: {self.amount}"


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
        indexes = [
            # Reconciliation's unmatched scan (council + UNMATCHED) and the
            # per-council feed lists order/window by received_at (PERF-2).
            models.Index(fields=["council", "received_at"]),
            models.Index(fields=["council", "match_status"]),
        ]
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

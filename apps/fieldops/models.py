from django.db import models

from apps.tenancy.models import CouncilScopedModel


class MobileSyncRecord(CouncilScopedModel):
    """One row per offline-queued record replayed through POST /mobile/sync —
    the idempotency ledger for the agent app's offline queue. Keyed on the
    client-generated `client_id` so a retried batch (e.g. the same sync call
    fired twice because the first response never made it back to a flaky
    connection) replays each record at most once, no matter which of
    ACCEPTED/CONFLICT/REJECTED it landed on the first time — see
    fieldops.services.replay_sync_record."""

    PAYMENT, PAYER = "PAYMENT", "PAYER"
    RECORD_TYPE_CHOICES = [(PAYMENT, "Payment"), (PAYER, "Payer")]

    ACCEPTED, CONFLICT, REJECTED = "ACCEPTED", "CONFLICT", "REJECTED"
    STATUS_CHOICES = [(ACCEPTED, "Accepted"), (CONFLICT, "Conflict"), (REJECTED, "Rejected")]

    client_id = models.CharField(max_length=64)
    record_type = models.CharField(max_length=16, choices=RECORD_TYPE_CHOICES)
    agent = models.ForeignKey("accounts.FieldAgent", on_delete=models.PROTECT, related_name="sync_records")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    result_ref = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mobile_sync_record"
        constraints = [
            models.UniqueConstraint(fields=["council", "client_id"], name="uniq_council_client_id"),
        ]

    def __str__(self):
        return f"{self.record_type}:{self.client_id} ({self.status})"

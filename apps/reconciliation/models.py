from django.db import models

from apps.tenancy.models import CouncilScopedModel


class ReconciliationRun(CouncilScopedModel):
    OPEN, BALANCED, EXCEPTIONS, CLOSED = "OPEN", "BALANCED", "EXCEPTIONS", "CLOSED"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (BALANCED, "Balanced"),
        (EXCEPTIONS, "Exceptions"),
        (CLOSED, "Closed"),
    ]

    channel = models.ForeignKey("payments.PaymentChannel", on_delete=models.PROTECT, related_name="reconciliation_runs")
    run_date = models.DateField()
    total_platform = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_bank = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=OPEN)
    run_by = models.ForeignKey("accounts.AppUser", on_delete=models.PROTECT, related_name="reconciliation_runs")

    class Meta:
        db_table = "reconciliation_run"
        constraints = [
            models.UniqueConstraint(fields=["council", "channel", "run_date"], name="uniq_recon_run_per_day"),
        ]
        ordering = ["-run_date"]

    def __str__(self):
        return f"{self.channel_id} {self.run_date} ({self.status})"


class ReconciliationException(CouncilScopedModel):
    run = models.ForeignKey(ReconciliationRun, on_delete=models.CASCADE, related_name="exceptions")
    feed_row = models.ForeignKey(
        "payments.ChannelTransactionFeed", on_delete=models.SET_NULL, null=True, blank=True, related_name="exceptions"
    )
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliation_exceptions"
    )
    note = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "accounts.AppUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_exceptions"
    )

    class Meta:
        db_table = "reconciliation_exception"

    def __str__(self):
        return f"Exception #{self.pk} (run {self.run_id})"

    @property
    def is_resolved(self):
        return self.resolved_at is not None

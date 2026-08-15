from django.db import models

from apps.tenancy.models import CouncilScopedModel


class CommissionSettlement(CouncilScopedModel):
    """One row per consultant per period. `commission_rate` is a *snapshot* at
    settlement time — not a live join to SubConsultant — so a later contract-rate
    change doesn't retroactively alter a past settlement."""

    COMPUTED, APPROVED, SETTLED, DISPUTED = "COMPUTED", "APPROVED", "SETTLED", "DISPUTED"
    STATUS_CHOICES = [
        (COMPUTED, "Computed"),
        (APPROVED, "Approved"),
        (SETTLED, "Settled"),
        (DISPUTED, "Disputed"),
    ]

    consultant = models.ForeignKey("accounts.SubConsultant", on_delete=models.PROTECT, related_name="settlements")
    period_start = models.DateField()
    period_end = models.DateField()
    gross_collections = models.DecimalField(max_digits=14, decimal_places=2)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=COMPUTED)
    computed_by = models.ForeignKey("accounts.AppUser", on_delete=models.PROTECT, related_name="settlements_computed")

    class Meta:
        db_table = "commission_settlement"
        constraints = [
            models.UniqueConstraint(
                fields=["consultant", "period_start", "period_end"], name="uniq_settlement_per_period"
            ),
        ]
        ordering = ["-period_start"]

    def __str__(self):
        return f"{self.consultant} {self.period_start}..{self.period_end} ({self.status})"

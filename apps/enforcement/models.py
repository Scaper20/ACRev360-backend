from django.db import models

from apps.tenancy.models import CouncilScopedModel


class DebtCase(CouncilScopedModel):
    """One per overdue bill. Escalation moves exactly one step at a time; a case
    auto-closes the moment its bill is fully paid (or superseded by an arrears
    consolidation), regardless of what stage it was at."""

    BUCKET_0_30, BUCKET_31_60, BUCKET_61_90, BUCKET_OVER_90 = "0_30", "31_60", "61_90", "OVER_90"
    AGEING_BUCKET_CHOICES = [
        (BUCKET_0_30, "0-30 days"),
        (BUCKET_31_60, "31-60 days"),
        (BUCKET_61_90, "61-90 days"),
        (BUCKET_OVER_90, "90+ days"),
    ]

    NONE, FIRST_NOTICE, FINAL_NOTICE, ENFORCEMENT, LEGAL, CLOSED = (
        "NONE", "FIRST_NOTICE", "FINAL_NOTICE", "ENFORCEMENT", "LEGAL", "CLOSED",
    )
    ENFORCEMENT_LADDER = [NONE, FIRST_NOTICE, FINAL_NOTICE, ENFORCEMENT, LEGAL, CLOSED]
    ENFORCEMENT_STAGE_CHOICES = [
        (NONE, "None"),
        (FIRST_NOTICE, "First Notice"),
        (FINAL_NOTICE, "Final Notice"),
        (ENFORCEMENT, "Enforcement"),
        (LEGAL, "Legal"),
        (CLOSED, "Closed"),
    ]

    bill = models.OneToOneField("billing.Bill", on_delete=models.CASCADE, related_name="debt_case")
    ageing_bucket = models.CharField(max_length=8, choices=AGEING_BUCKET_CHOICES, default=BUCKET_0_30)
    enforcement_stage = models.CharField(max_length=16, choices=ENFORCEMENT_STAGE_CHOICES, default=FIRST_NOTICE)
    reminder_count = models.PositiveIntegerField(default=0)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "debt_case"

    def __str__(self):
        return f"DebtCase({self.bill.bill_ref}, {self.enforcement_stage})"

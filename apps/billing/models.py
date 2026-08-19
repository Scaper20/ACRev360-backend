from django.db import models

from apps.tenancy.models import CouncilScopedModel


class Assessment(CouncilScopedModel):
    """One line of 'payer X owes Y for revenue item Z', priced against a specific
    rate_schedule row. DRAFT is exactly what enumeration creates and what "Issue
    Harmonized Bill" rolls up; APPROVED is reserved, unused by this build."""

    DRAFT, APPROVED, BILLED, CANCELLED = "DRAFT", "APPROVED", "BILLED", "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (APPROVED, "Approved"),
        (BILLED, "Billed"),
        (CANCELLED, "Cancelled"),
    ]

    payer = models.ForeignKey("registry.Payer", on_delete=models.CASCADE, related_name="assessments")
    council_revenue_item = models.ForeignKey(
        "revenue.CouncilRevenueItem", on_delete=models.PROTECT, related_name="assessments"
    )
    # Exactly one pricing source is ever set: rate_schedule for a plain FLAT item,
    # or rate_band (+ rate_tier when that band is TIERED) for a banded one — see
    # apps.billing.services.create_draft_assessment. Both null is never valid;
    # enforced in code, not a DB constraint, since which one applies depends on
    # the item's band state *at assessment time*, not a fact about this row alone.
    rate_schedule = models.ForeignKey(
        "revenue.RateSchedule", on_delete=models.PROTECT, null=True, blank=True, related_name="assessments"
    )
    rate_band = models.ForeignKey(
        "revenue.RateBand", on_delete=models.PROTECT, null=True, blank=True, related_name="assessments"
    )
    rate_tier = models.ForeignKey(
        "revenue.RateTier", on_delete=models.PROTECT, null=True, blank=True, related_name="assessments"
    )
    asset = models.ForeignKey(
        "registry.EnumeratedAsset", on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments"
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=DRAFT)
    created_by = models.ForeignKey("accounts.AppUser", on_delete=models.PROTECT, related_name="assessments_created")

    class Meta:
        db_table = "assessment"

    def __str__(self):
        return f"{self.payer_id}:{self.council_revenue_item.harmonised_code} = {self.amount}"


class Bill(CouncilScopedModel):
    """The harmonised, payable unit. See V2_ARCHITECTURE.md §7 for the invariants
    this model exists to protect: terminal states refuse payment, arrears
    consolidation never double-counts."""

    ISSUED, PART_PAID, PAID, OVERDUE, CANCELLED, SUPERSEDED = (
        "ISSUED", "PART_PAID", "PAID", "OVERDUE", "CANCELLED", "SUPERSEDED",
    )
    STATUS_CHOICES = [
        (ISSUED, "Issued"),
        (PART_PAID, "Part Paid"),
        (PAID, "Paid"),
        (OVERDUE, "Overdue"),
        (CANCELLED, "Cancelled"),
        (SUPERSEDED, "Superseded"),
    ]
    TERMINAL_STATUSES = {CANCELLED, SUPERSEDED}

    bill_ref = models.CharField(max_length=64, unique=True, blank=True)
    payer = models.ForeignKey("registry.Payer", on_delete=models.PROTECT, related_name="bills")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    arrears_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ISSUED)
    due_date = models.DateField()
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes"
    )
    issued_by = models.ForeignKey("accounts.AppUser", on_delete=models.PROTECT, related_name="bills_issued")

    class Meta:
        db_table = "bill"
        indexes = [
            models.Index(fields=["council", "status"]),
            models.Index(fields=["council", "payer"]),
        ]

    def __str__(self):
        return self.bill_ref or f"(unsaved bill #{self.pk})"

    @property
    def balance(self):
        return self.total_amount - self.amount_paid


class BillLine(models.Model):
    """Join table between bill and assessment — each line carries its own
    `line_amount`, which can be admin-overridden per payer without touching the
    item's standard rate."""

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="lines")
    assessment = models.ForeignKey("billing.Assessment", on_delete=models.PROTECT, related_name="bill_lines")
    line_amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        db_table = "bill_line"

    def __str__(self):
        return f"{self.bill.bill_ref} line {self.pk}"

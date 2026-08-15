from django.db import models

from apps.common.models import TimeStampedModel
from apps.tenancy.models import CouncilScopedModel, WardZone


class RevenueCategory(models.Model):
    """Rates, Licences and Permits, Fees and Charges, Registration and Professional
    Fees, Levies — council-agnostic groupings, same across every council."""

    name = models.CharField(max_length=120, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "revenue_category"
        ordering = ["sort_order", "name"]
        verbose_name_plural = "revenue categories"

    def __str__(self):
        return self.name


class RevenueItemTemplate(TimeStampedModel):
    """
    The global, harmonised item definition — code, name, category, unit. Councils
    activate items from this template rather than defining their own from scratch,
    so cross-council comparison ("Liquor Licensing yield across the FCT") stays a
    simple query. See V2_ARCHITECTURE.md §4.1.
    """

    category = models.ForeignKey(RevenueCategory, on_delete=models.PROTECT, related_name="item_templates")
    harmonised_code = models.CharField(max_length=32, unique=True)
    item_name = models.CharField(max_length=160)
    unit_of_charge = models.CharField(max_length=64)
    in_initial_scope = models.BooleanField(default=True)

    class Meta:
        db_table = "revenue_item_template"
        ordering = ["harmonised_code"]

    def __str__(self):
        return f"{self.harmonised_code} {self.item_name}"


class CouncilRevenueItem(CouncilScopedModel):
    """
    A council's activation of a template item (or a council-local item that exists
    outside the template — `template` is null in that case). `rate_schedule` hangs
    off this row, not off the template, so Kuje's price and AMAC's price are two
    independent rate histories against one shared definition.
    """

    template = models.ForeignKey(
        RevenueItemTemplate, on_delete=models.PROTECT, null=True, blank=True, related_name="council_activations"
    )
    harmonised_code = models.CharField(max_length=32)
    item_name = models.CharField(max_length=160)
    category = models.ForeignKey(RevenueCategory, on_delete=models.PROTECT, related_name="council_items")
    unit_of_charge = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "council_revenue_item"
        constraints = [
            models.UniqueConstraint(fields=["council", "harmonised_code"], name="uniq_item_code_per_council"),
        ]
        ordering = ["harmonised_code"]

    def __str__(self):
        return f"{self.harmonised_code} {self.item_name} ({self.council.council_code})"

    @property
    def current_rate(self):
        return self.rate_schedules.filter(effective_to__isnull=True).order_by("-effective_from").first()


class RateSchedule(TimeStampedModel):
    """
    Price *history*, not a single field. Changing a rate never overwrites a row: it
    closes the current one (`effective_to`) and opens a new one, so assessments
    always cite the exact rate they were priced at, even years later. See
    V2_ARCHITECTURE.md §7.6.
    """

    council_revenue_item = models.ForeignKey(
        CouncilRevenueItem, on_delete=models.PROTECT, related_name="rate_schedules"
    )
    rate_amount = models.DecimalField(max_digits=14, decimal_places=2)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "rate_schedule"
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["council_revenue_item"],
                condition=models.Q(effective_to__isnull=True),
                name="uniq_open_rate_per_item",
            ),
        ]

    def __str__(self):
        return f"{self.council_revenue_item.harmonised_code} @ {self.rate_amount}"


class ConsultantPortfolio(CouncilScopedModel):
    """Which revenue items (optionally ward-scoped) a consultant may handle.
    Ending an assignment sets `effective_to` rather than deleting — history kept."""

    consultant = models.ForeignKey("accounts.SubConsultant", on_delete=models.CASCADE, related_name="portfolio")
    council_revenue_item = models.ForeignKey(CouncilRevenueItem, on_delete=models.PROTECT, related_name="portfolio_entries")
    ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, null=True, blank=True, related_name="portfolio_entries")
    effective_from = models.DateField(auto_now_add=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "consultant_portfolio"
        ordering = ["-effective_from"]

    def __str__(self):
        return f"{self.consultant} -> {self.council_revenue_item}"

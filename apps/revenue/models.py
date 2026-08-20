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

    @property
    def active_bands(self):
        return self.rate_bands.filter(effective_to__isnull=True).order_by("sort_order", "label")


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


class RateBand(TimeStampedModel):
    """
    A named sub-classification of a revenue item, priced independently of the
    item's plain `RateSchedule` — e.g. "Beer parlor" under Liquor Licensing, or
    "School Sign Board" under Control of Advertisement, each gazetted with its own
    minimum/maximum or small/medium/large figures rather than one flat rate for
    the whole item.

    An item with zero open bands still prices from `RateSchedule` exactly as
    before (`FLAT`, single number). An item with one or more open bands requires
    the assessing agent to pick a band; `RANGE` bands additionally require an
    amount within [min_amount, max_amount], and `TIERED` bands require picking
    one of the band's `RateTier` rows instead of typing a number.

    Versioned the same way as `RateSchedule` and for the same reason: never
    mutate a band in place, close it (`effective_to`) and open a new one, so an
    assessment's `rate_band`/`rate_tier` FK always points at the exact figures it
    was priced against, even after the council amends the bye-law. A whole item's
    band set is replaced together (`revenue.services.replace_rate_bands`), which
    mirrors how a gazette amendment supersedes a whole schedule at once rather
    than editing individual cells.
    """

    FLAT, RANGE, TIERED = "FLAT", "RANGE", "TIERED"
    RATE_MODE_CHOICES = [(FLAT, "Flat"), (RANGE, "Range"), (TIERED, "Tiered")]

    council_revenue_item = models.ForeignKey(
        CouncilRevenueItem, on_delete=models.CASCADE, related_name="rate_bands"
    )
    label = models.CharField(
        max_length=160, blank=True,
        help_text="The gazette's sub-classification name, e.g. 'Beer parlor'. Blank only when "
        "the item has exactly one band standing in for the whole item (no sub-classification), "
        "e.g. Communication Mast's single Large/Medium/Small triple.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    rate_mode = models.CharField(max_length=16, choices=RATE_MODE_CHOICES)
    flat_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    min_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "rate_band"
        ordering = ["sort_order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["council_revenue_item", "label"],
                condition=models.Q(effective_to__isnull=True),
                name="uniq_open_band_label_per_item",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(rate_mode="FLAT", flat_amount__isnull=False, min_amount__isnull=True, max_amount__isnull=True)
                    | models.Q(rate_mode="RANGE", flat_amount__isnull=True, min_amount__isnull=False, max_amount__isnull=False)
                    | models.Q(rate_mode="TIERED", flat_amount__isnull=True, min_amount__isnull=True, max_amount__isnull=True)
                ),
                name="rate_band_amount_fields_match_mode",
            ),
        ]

    def __str__(self):
        return f"{self.council_revenue_item.harmonised_code} — {self.label or '(unlabeled)'}"


class RateTier(TimeStampedModel):
    """
    One labeled amount within a `TIERED` `RateBand` — "Small"/"Medium"/"Large" for
    Liquor Licensing, "Rural"/"Semi Urban"/"Urban" for Tenement Rate Collection's
    location-tiered categories. Not a fixed enum on purpose: different bye-laws
    tier by different things, and the label is exactly what the agent sees when
    picking one during assessment.
    """

    band = models.ForeignKey(RateBand, on_delete=models.CASCADE, related_name="tiers")
    label = models.CharField(max_length=40)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "rate_tier"
        ordering = ["sort_order", "label"]
        constraints = [
            models.UniqueConstraint(fields=["band", "label"], name="uniq_tier_label_per_band"),
        ]

    def __str__(self):
        return f"{self.band} · {self.label} = {self.amount}"


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


class AgentPortfolio(CouncilScopedModel):
    """Which revenue items (optionally ward-scoped) a field agent may handle —
    a further, optional narrowing of their own consultant's ConsultantPortfolio,
    not an independent grant. An agent with no rows here still has their whole
    consultant's portfolio (see CouncilRevenueItemViewSet.get_queryset()); an
    agent with at least one row is restricted to exactly those. Assigning
    requires the item to already be in the agent's consultant's active
    portfolio — see FieldAgentViewSet.portfolio(). Ending an assignment sets
    `effective_to` rather than deleting — history kept."""

    agent = models.ForeignKey("accounts.FieldAgent", on_delete=models.CASCADE, related_name="portfolio")
    council_revenue_item = models.ForeignKey(CouncilRevenueItem, on_delete=models.PROTECT, related_name="agent_portfolio_entries")
    ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, null=True, blank=True, related_name="agent_portfolio_entries")
    effective_from = models.DateField(auto_now_add=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "agent_portfolio"
        ordering = ["-effective_from"]

    def __str__(self):
        return f"{self.agent} -> {self.council_revenue_item}"

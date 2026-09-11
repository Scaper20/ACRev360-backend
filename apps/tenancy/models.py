from django.db import models

from apps.common.models import TimeStampedModel


class Council(TimeStampedModel):
    """The tenant. One row per FCT Area Council — Kuje (KAC) is #1."""

    council_code = models.CharField(max_length=16, unique=True)
    council_name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "council"
        ordering = ["council_code"]

    def __str__(self):
        return self.council_code


class CouncilScopedModel(TimeStampedModel):
    """
    Abstract base for every tenant-scoped table. Concrete subclasses get an
    RLS policy added in their own migration via apps.common.db.enable_tenant_rls_sql
    — see V2_ARCHITECTURE.md §3.
    """

    council = models.ForeignKey(Council, on_delete=models.PROTECT)

    class Meta:
        abstract = True


class WardZone(CouncilScopedModel):
    ZONE_TYPE_CHOICES = [
        ("WARD", "Ward"),
        ("ZONE", "Zone"),
        ("DISTRICT", "District"),
    ]

    ward_code = models.CharField(max_length=32)
    ward_name = models.CharField(max_length=120)
    zone_type = models.CharField(max_length=16, choices=ZONE_TYPE_CHOICES, default="WARD")

    class Meta:
        db_table = "ward_zone"
        constraints = [
            models.UniqueConstraint(fields=["council", "ward_code"], name="uniq_ward_code_per_council"),
        ]
        ordering = ["ward_name"]

    def __str__(self):
        return self.ward_name


class Department(CouncilScopedModel):
    """A council administrative department that revenue items can be grouped
    under — see CouncilRevenueItem.department. Council-scoped like every other
    reference model here (WardZone included) since departmental structure
    varies council to council."""

    department_name = models.CharField(max_length=160)
    department_code = models.CharField(max_length=32, blank=True)
    head_name = models.CharField(max_length=160, blank=True)
    head_phone = models.CharField(max_length=32, blank=True)
    legal_basis = models.CharField(
        max_length=120, blank=True,
        help_text="The bye-law provision constituting this department, e.g. "
        "'Part II, Section 6' — from the council's departmental schedule.",
    )

    class Meta:
        db_table = "department"
        constraints = [
            models.UniqueConstraint(fields=["council", "department_name"], name="uniq_department_name_per_council"),
        ]
        ordering = ["department_name"]

    def __str__(self):
        return self.department_name


class CouncilConfig(TimeStampedModel):
    """
    Everything that was hardcoded for KAC in the prototype becomes a per-council
    config record — see V2_ARCHITECTURE.md §4.2. No code path may branch on
    "which council is this"; it reads this instead.
    """

    council = models.OneToOneField(Council, on_delete=models.CASCADE, related_name="config")

    bill_ref_prefix = models.CharField(
        max_length=16, unique=True, help_text="e.g. KAC — used as bill_ref's leading segment"
    )
    bill_due_days = models.PositiveSmallIntegerField(default=30)

    revenue_bank_name = models.CharField(max_length=120, blank=True)
    revenue_bank_account_number = models.CharField(max_length=32, blank=True)
    revenue_bank_account_name = models.CharField(max_length=160, blank=True)

    treasurer_name = models.CharField(max_length=120, blank=True)
    treasurer_phone = models.CharField(max_length=32, blank=True)

    print_signatory_name = models.CharField(max_length=120, blank=True)
    print_signatory_title = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "council_config"

    def __str__(self):
        return f"Config({self.council.council_code})"

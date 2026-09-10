from django.db import models

from apps.tenancy.models import CouncilScopedModel, WardZone


class Payer(CouncilScopedModel):
    INDIVIDUAL, BUSINESS, GOVERNMENT, NGO = "INDIVIDUAL", "BUSINESS", "GOVERNMENT", "NGO"
    PAYER_TYPE_CHOICES = [
        (INDIVIDUAL, "Individual"),
        (BUSINESS, "Business"),
        (GOVERNMENT, "Government"),
        (NGO, "NGO"),
    ]

    MICRO, SMALL, MEDIUM, LARGE = "MICRO", "SMALL", "MEDIUM", "LARGE"
    BUSINESS_SIZE_CHOICES = [
        (MICRO, "Micro"),
        (SMALL, "Small"),
        (MEDIUM, "Medium"),
        (LARGE, "Large"),
    ]

    PENDING, VERIFIED, FLAGGED = "PENDING", "VERIFIED", "FLAGGED"
    KYC_STATUS_CHOICES = [
        (PENDING, "Pending"),
        (VERIFIED, "Verified"),
        (FLAGGED, "Flagged"),
    ]

    payer_ref = models.CharField(max_length=48, unique=True, blank=True)
    payer_type = models.CharField(max_length=16, choices=PAYER_TYPE_CHOICES)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    #: A single-token name (registered businesses, most GOVERNMENT/NGO payers)
    #: lives entirely in first_name, with last_name left blank — see the
    #: 0006 data migration for the same rule applied to pre-existing rows.
    last_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, related_name="payers")

    # Individuals only:
    nin_bvn_hash = models.CharField(max_length=128, blank=True)
    # Non-individuals only:
    tin = models.CharField(max_length=32, blank=True)
    business_size = models.CharField(max_length=16, choices=BUSINESS_SIZE_CHOICES, blank=True, null=True)

    kyc_status = models.CharField(max_length=16, choices=KYC_STATUS_CHOICES, default=PENDING)
    is_duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates"
    )

    enumerated_by = models.ForeignKey(
        "accounts.AppUser", on_delete=models.PROTECT, related_name="enumerated_payers"
    )

    #: Set via FieldAgentViewSet.assign_payer — once set, this payer comes out
    #: of the general consultant-team pool for portfolio-scoping purposes
    #: (see apps.common.scoping.portfolio_filter): only this agent (and the
    #: consultant manager, who still sees the whole team) can see it, even if
    #: a *different* agent originally registered it (enumerated_by).
    assigned_agent = models.ForeignKey(
        "accounts.AppUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_payers"
    )

    #: Unapplied money — the leftover when a payment exceeds a bill's
    #: outstanding balance. Not auto-consumed against a future bill; that's
    #: a separate feature. See apps.payments.services.post_payment.
    credit_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = "payer"
        indexes = [
            models.Index(fields=["council", "last_name", "first_name"]),
            models.Index(fields=["council", "phone"]),
        ]

    def __str__(self):
        return f"{self.payer_ref or '(unsaved)'} {self.full_name}"

    @property
    def full_name(self) -> str:
        """Read-only display reconstruction — every consumer that only ever
        read this name (receipts, bill/payment/debt serializers, search
        result labels) keeps working unchanged; only ORM-level filtering and
        ordering had to move to the real first_name/middle_name/last_name
        columns, since a property isn't queryable at the database level."""
        return " ".join(part for part in (self.first_name, self.middle_name, self.last_name) if part)


class EnumeratedAsset(CouncilScopedModel):
    """A physical thing tied to a payer (premises, shop, kiosk, signage), captured
    with GPS at enumeration time."""

    PREMISES, SHOP, KIOSK, SIGNAGE = "PREMISES", "SHOP", "KIOSK", "SIGNAGE"
    ASSET_TYPE_CHOICES = [
        (PREMISES, "Premises"),
        (SHOP, "Shop"),
        (KIOSK, "Kiosk"),
        (SIGNAGE, "Signage"),
    ]

    payer = models.ForeignKey(Payer, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=16, choices=ASSET_TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, related_name="assets")
    geo_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = "enumerated_asset"

    def __str__(self):
        return f"{self.asset_type} — {self.payer.full_name}"

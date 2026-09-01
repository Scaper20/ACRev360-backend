from decimal import Decimal

from rest_framework import serializers

from apps.billing.models import Bill, BillLine


class BillSerializer(serializers.ModelSerializer):
    payer_ref = serializers.CharField(source="payer.payer_ref", read_only=True)
    full_name = serializers.CharField(source="payer.full_name", read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    consultant_name = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = [
            "id", "bill_ref", "payer", "payer_ref", "full_name", "total_amount", "amount_paid",
            "arrears_amount", "balance", "status", "due_date", "superseded_by", "created_at",
            "consultant_name",
        ]
        read_only_fields = fields

    def get_consultant_name(self, obj):
        consultant = getattr(obj.payer.enumerated_by, "consultant", None)
        return consultant.consultant_name if consultant else None


class BillLineDetailSerializer(serializers.ModelSerializer):
    harmonised_code = serializers.CharField(source="assessment.council_revenue_item.harmonised_code", read_only=True)
    item_name = serializers.CharField(source="assessment.council_revenue_item.item_name", read_only=True)
    quantity = serializers.DecimalField(source="assessment.quantity", max_digits=10, decimal_places=2, read_only=True)
    band_label = serializers.CharField(source="assessment.rate_band.label", read_only=True, default=None)
    tier_label = serializers.CharField(source="assessment.rate_tier.label", read_only=True, default=None)

    class Meta:
        model = BillLine
        fields = ["id", "assessment", "harmonised_code", "item_name", "quantity", "line_amount", "band_label", "tier_label"]
        read_only_fields = ["id", "assessment", "harmonised_code", "item_name", "quantity", "band_label", "tier_label"]


class SupersededBillSerializer(serializers.Serializer):
    """One prior bill folded into this one's arrears_amount via roll_arrears —
    see billing.services.issue_bill. `amount` reads the prior bill's own
    `balance` (aliased — a SUPERSEDED bill isn't itself called "amount"
    anything), frozen at whatever it was the moment it was superseded (a
    SUPERSEDED bill can never take a further payment — see post_payment's
    terminal-state check — so its current balance is exactly what got rolled
    in). Takes Bill instances directly (e.g. `bill.supersedes.all()`), not
    hand-built dicts — a dict would need this same "amount" key, and DRF's
    DecimalField-to-string formatting only happens by going through this
    serializer, not by building a response dict by hand (see the git history
    on PublicBillLookupView for the bug that shipped from doing that once).

    `lines` exposes each superseded bill's own itemized BillLines — issue_bill's
    roll_arrears never touches them, it only flips the prior bill's status to
    SUPERSEDED and sums its balance into the new bill's arrears_amount. So the
    line-level detail behind that lump sum was always sitting right here,
    unexposed — this is a read-only addition, no new storage or change to
    roll_arrears itself."""

    bill_ref = serializers.CharField()
    amount = serializers.DecimalField(source="balance", max_digits=14, decimal_places=2)
    lines = BillLineDetailSerializer(many=True, read_only=True)


class BillDetailSerializer(BillSerializer):
    lines = BillLineDetailSerializer(many=True, read_only=True)
    # Only on the detail serializer, not the list one — this is a breakdown
    # someone looks up when they need it, not something worth an extra query
    # per row of a bill list. source="supersedes" is the reverse of
    # Bill.superseded_by — DRF calls .all() on it automatically since this is
    # a many=True nested serializer over a related manager, same as `lines`.
    superseded_bills = SupersededBillSerializer(source="supersedes", many=True, read_only=True)

    class Meta(BillSerializer.Meta):
        fields = BillSerializer.Meta.fields + ["lines", "superseded_bills"]
        read_only_fields = fields


# rate_band_id/rate_tier_id/amount_override are only required when the chosen
# item has open rate bands — see CouncilRevenueItem.active_bands and
# apps.billing.services.create_draft_assessment, which does the real
# validation (band belongs to the item, amount within range, tier belongs to
# band). Omitted entirely for a plain FLAT item.
class BillLineEntrySerializer(serializers.Serializer):
    revenue_item_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=1, min_value=Decimal("0.01"))
    rate_band_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    rate_tier_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    amount_override = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True, default=None)


class IssueBillSerializer(serializers.Serializer):
    payer_id = serializers.IntegerField()
    due_date = serializers.DateField(required=False)
    lines = BillLineEntrySerializer(many=True, required=False, default=list)
    bill_all_drafts = serializers.BooleanField(default=False)
    roll_arrears = serializers.BooleanField(default=False)


class AddLineSerializer(serializers.Serializer):
    revenue_item_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=1, min_value=Decimal("0.01"))
    rate_band_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    rate_tier_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    amount_override = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True, default=None)


class UpdateLineSerializer(serializers.Serializer):
    line_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)


class PublicBillLookupSerializer(serializers.Serializer):
    """Shapes the public (unauthenticated) bill lookup response — payer identity
    plus bill/lines/arrears, matching what the print pages need."""

    bill_ref = serializers.CharField()
    status = serializers.CharField()
    due_date = serializers.DateField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    arrears_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    payer_ref = serializers.CharField()
    full_name = serializers.CharField()
    phone = serializers.CharField()
    address = serializers.CharField()
    ward_name = serializers.CharField()
    lines = BillLineDetailSerializer(many=True)
    superseded_bills = SupersededBillSerializer(many=True)

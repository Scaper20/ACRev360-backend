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


class BillDetailSerializer(BillSerializer):
    lines = BillLineDetailSerializer(many=True, read_only=True)

    class Meta(BillSerializer.Meta):
        fields = BillSerializer.Meta.fields + ["lines"]
        read_only_fields = fields


# rate_band_id/rate_tier_id/amount_override are only required when the chosen
# item has open rate bands — see CouncilRevenueItem.active_bands and
# apps.billing.services.create_draft_assessment, which does the real
# validation (band belongs to the item, amount within range, tier belongs to
# band). Omitted entirely for a plain FLAT item.
class BillLineEntrySerializer(serializers.Serializer):
    revenue_item_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=1)
    rate_band_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    rate_tier_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    amount_override = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True, default=None)


class IssueBillSerializer(serializers.Serializer):
    payer_id = serializers.IntegerField()
    due_date = serializers.DateField(required=False)
    lines = BillLineEntrySerializer(many=True, required=False, default=list)
    bill_all_drafts = serializers.BooleanField(default=False)
    roll_arrears = serializers.BooleanField(default=False)


class AddLineSerializer(serializers.Serializer):
    revenue_item_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=1)
    rate_band_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    rate_tier_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    amount_override = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True, default=None)


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

from rest_framework import serializers

from apps.registry.models import EnumeratedAsset, Payer


class PayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payer
        fields = [
            "id", "payer_ref", "payer_type", "full_name", "phone", "address", "ward",
            "nin_bvn_hash", "tin", "business_size", "kyc_status", "created_at",
        ]
        read_only_fields = ["id", "payer_ref", "kyc_status", "created_at"]


class CreatePayerSerializer(serializers.ModelSerializer):
    revenue_item_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    force = serializers.BooleanField(required=False, default=False, write_only=True)
    # COUNCIL_ADMIN-only — assigns the payer to a consultant's portfolio at
    # registration time. Ignored (not read) for any other caller; see
    # PayerViewSet.create(). Not a plain FK field since the value actually
    # stored is enumerated_by (an AppUser), not this SubConsultant id
    # directly — the view resolves one from the other.
    assigned_consultant_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Payer
        fields = [
            "payer_type", "full_name", "phone", "address", "ward",
            "nin_bvn_hash", "tin", "business_size", "revenue_item_ids", "force", "assigned_consultant_id",
        ]


class PayerCreateResponseSerializer(PayerSerializer):
    draft_assessments_created = serializers.IntegerField(read_only=True)

    class Meta(PayerSerializer.Meta):
        fields = PayerSerializer.Meta.fields + ["draft_assessments_created"]


class DuplicatePayerResponseSerializer(serializers.Serializer):
    error = serializers.CharField()
    duplicate_of = PayerSerializer()


class KycStatusSerializer(serializers.Serializer):
    kyc_status = serializers.ChoiceField(choices=Payer.KYC_STATUS_CHOICES)


class EnumeratedAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnumeratedAsset
        fields = ["id", "payer", "asset_type", "description", "ward", "geo_lat", "geo_lng"]
        read_only_fields = ["id"]


class DraftAssessmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    council_revenue_item_id = serializers.IntegerField()
    harmonised_code = serializers.CharField()
    item_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)

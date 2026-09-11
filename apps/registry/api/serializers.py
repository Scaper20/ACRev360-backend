from rest_framework import serializers

from apps.registry.models import EnumeratedAsset, Payer, PayerDelegation


class PayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payer
        fields = [
            "id", "payer_ref", "payer_type", "first_name", "middle_name", "last_name", "full_name",
            "phone", "email", "address", "ward", "nin_bvn_hash", "tin", "business_size", "line_of_business",
            "kyc_status", "created_at",
        ]
        # full_name isn't a model field (it's a read-only display property —
        # see Payer.full_name) so DRF already treats it as read-only
        # automatically; listed here for clarity all the same.
        read_only_fields = ["id", "payer_ref", "kyc_status", "created_at", "full_name"]


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
            "payer_type", "first_name", "middle_name", "last_name", "phone", "email", "address", "ward",
            "nin_bvn_hash", "tin", "business_size", "line_of_business", "revenue_item_ids", "force",
            "assigned_consultant_id",
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


class InviteRatepayerSerializer(serializers.Serializer):
    """Write-only shape for PayerViewSet.invite_ratepayer — same pattern as
    FieldAgentViewSet's write-only username/password create fields."""

    username = serializers.CharField(max_length=64)
    password = serializers.CharField(write_only=True)


class PayerDelegationSerializer(serializers.ModelSerializer):
    proxy_email = serializers.CharField(source="proxy_user.email", read_only=True)
    proxy_full_name = serializers.CharField(source="proxy_user.full_name", read_only=True)

    class Meta:
        model = PayerDelegation
        fields = ["id", "payer", "proxy_user", "proxy_email", "proxy_full_name", "granted_at", "revoked_at"]
        read_only_fields = fields


class CreateDelegationSerializer(serializers.Serializer):
    #: Resolved to an existing AppUser with access_level=RATEPAYER_PROXY by
    #: the view — never auto-created, since a proxy must already hold their
    #: own login before a ratepayer can delegate to them.
    proxy_email = serializers.EmailField()


class DraftAssessmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    council_revenue_item_id = serializers.IntegerField()
    harmonised_code = serializers.CharField()
    item_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)

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

    class Meta:
        model = Payer
        fields = [
            "payer_type", "full_name", "phone", "address", "ward",
            "nin_bvn_hash", "tin", "business_size", "revenue_item_ids", "force",
        ]


class EnumeratedAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnumeratedAsset
        fields = ["id", "payer", "asset_type", "description", "ward", "geo_lat", "geo_lng"]
        read_only_fields = ["id"]

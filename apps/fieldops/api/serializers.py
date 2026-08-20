from rest_framework import serializers

from apps.fieldops.models import MobileSyncRecord
from apps.registry.models import Payer


class WorklistPayerSerializer(serializers.ModelSerializer):
    ward_name = serializers.CharField(source="ward.ward_name", read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Payer
        fields = ["id", "payer_ref", "payer_type", "full_name", "phone", "address", "ward", "ward_name", "outstanding", "kyc_status"]
        read_only_fields = fields


class SyncRecordInputSerializer(serializers.Serializer):
    client_id = serializers.CharField(max_length=64)
    entity_type = serializers.ChoiceField(choices=MobileSyncRecord.RECORD_TYPE_CHOICES)
    payload = serializers.DictField()


class SyncRequestSerializer(serializers.Serializer):
    records = SyncRecordInputSerializer(many=True)


class SyncOutcomeSerializer(serializers.Serializer):
    client_id = serializers.CharField()
    result_ref = serializers.CharField(allow_blank=True)
    detail = serializers.DictField()


class SyncResponseSerializer(serializers.Serializer):
    accepted = SyncOutcomeSerializer(many=True)
    conflicts = SyncOutcomeSerializer(many=True)
    rejected = SyncOutcomeSerializer(many=True)

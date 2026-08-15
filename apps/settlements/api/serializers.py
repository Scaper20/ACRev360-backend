from rest_framework import serializers

from apps.settlements.models import CommissionSettlement


class CommissionSettlementSerializer(serializers.ModelSerializer):
    consultant_name = serializers.CharField(source="consultant.consultant_name", read_only=True)

    class Meta:
        model = CommissionSettlement
        fields = [
            "id", "consultant", "consultant_name", "period_start", "period_end",
            "gross_collections", "commission_rate", "commission_amount", "status",
        ]
        read_only_fields = fields


class ComputeSettlementsSerializer(serializers.Serializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()


class SettlementStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=CommissionSettlement.STATUS_CHOICES)

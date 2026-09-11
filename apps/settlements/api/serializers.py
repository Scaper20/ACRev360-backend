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


class SettlementBillSerializer(serializers.Serializer):
    bill_id = serializers.IntegerField()
    bill_ref = serializers.CharField()
    payer_name = serializers.CharField()
    collected = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    status = serializers.ChoiceField(choices=CommissionSettlement.STATUS_CHOICES)


class MySettlementSummarySerializer(serializers.Serializer):
    total_this_year = serializers.DecimalField(max_digits=14, decimal_places=2)
    approved_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    settled_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    bills = SettlementBillSerializer(many=True)

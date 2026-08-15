from rest_framework import serializers

from apps.enforcement.models import DebtCase


class DebtCaseSerializer(serializers.ModelSerializer):
    bill_ref = serializers.CharField(source="bill.bill_ref", read_only=True)
    full_name = serializers.CharField(source="bill.payer.full_name", read_only=True)
    balance = serializers.DecimalField(source="bill.balance", max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = DebtCase
        fields = [
            "id", "bill", "bill_ref", "full_name", "balance",
            "ageing_bucket", "enforcement_stage", "reminder_count", "opened_at", "closed_at",
        ]
        read_only_fields = fields

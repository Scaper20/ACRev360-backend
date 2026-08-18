from rest_framework import serializers

from apps.reconciliation.models import ReconciliationException, ReconciliationRun


class ReconciliationExceptionSerializer(serializers.ModelSerializer):
    bank_txn_ref = serializers.CharField(source="feed_row.bank_txn_ref", read_only=True, default=None)
    amount = serializers.DecimalField(source="feed_row.amount", max_digits=14, decimal_places=2, read_only=True, default=None)

    class Meta:
        model = ReconciliationException
        fields = ["id", "run", "feed_row", "bank_txn_ref", "amount", "payment", "note", "resolved_at", "resolved_by"]
        read_only_fields = ["id", "run", "feed_row", "bank_txn_ref", "amount"]


class GlobalExceptionSerializer(serializers.ModelSerializer):
    """Cross-run view of exceptions — separate from ReconciliationExceptionSerializer
    (nested under a run) since here each row needs to identify its own run."""

    bank_txn_ref = serializers.CharField(source="feed_row.bank_txn_ref", read_only=True, default=None)
    amount = serializers.DecimalField(source="feed_row.amount", max_digits=14, decimal_places=2, read_only=True, default=None)
    channel_code = serializers.CharField(source="run.channel.code", read_only=True)
    run_date = serializers.DateField(source="run.run_date", read_only=True)

    class Meta:
        model = ReconciliationException
        fields = [
            "id", "run", "channel_code", "run_date", "feed_row", "bank_txn_ref", "amount",
            "payment", "note", "resolved_at", "resolved_by",
        ]
        read_only_fields = fields


class ReconciliationRunSerializer(serializers.ModelSerializer):
    channel_code = serializers.CharField(source="channel.code", read_only=True)
    exceptions = ReconciliationExceptionSerializer(many=True, read_only=True)

    class Meta:
        model = ReconciliationRun
        fields = ["id", "channel", "channel_code", "run_date", "total_platform", "total_bank", "status", "exceptions"]
        read_only_fields = fields


class RunReconciliationSerializer(serializers.Serializer):
    date = serializers.DateField()
    channel_code = serializers.CharField()


class ResolveExceptionSerializer(serializers.Serializer):
    note = serializers.CharField()

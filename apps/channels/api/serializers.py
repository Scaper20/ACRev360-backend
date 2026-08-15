from rest_framework import serializers


class ChannelCatalogueEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    code = serializers.CharField()
    label = serializers.CharField()
    required_fields = serializers.ListField(child=serializers.CharField())


class WebhookResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["posted", "duplicate", "accepted_unmatched", "rejected"])
    paymentRef = serializers.CharField(required=False)
    receiptRef = serializers.CharField(required=False)
    verifyToken = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    bank_txn_ref = serializers.CharField(required=False)


class OTCSettlementRowSerializer(serializers.Serializer):
    tellerRef = serializers.CharField()
    branchCode = serializers.CharField()
    amount = serializers.FloatField()
    billRef = serializers.CharField()


class OTCSettlementExceptionSerializer(serializers.Serializer):
    bill_ref = serializers.CharField(required=False)
    row = serializers.DictField(required=False)
    error = serializers.CharField()


class OTCSettlementResponseSerializer(serializers.Serializer):
    posted = serializers.IntegerField()
    duplicates_skipped = serializers.IntegerField()
    exceptions = OTCSettlementExceptionSerializer(many=True)


class USSDSessionRequestSerializer(serializers.Serializer):
    text = serializers.CharField(help_text="Accumulated USSD input, e.g. '1*KAC/2026/000001*5000'")
    msisdn = serializers.CharField()

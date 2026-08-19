from decimal import Decimal

from rest_framework import serializers

from apps.payments.models import APIClient, PaymentChannel, POSTerminal, Payment, Receipt


class PaymentChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentChannel
        fields = ["id", "code", "provider"]


class PaymentSerializer(serializers.ModelSerializer):
    bill_ref = serializers.CharField(source="bill.bill_ref", read_only=True)
    channel_code = serializers.CharField(source="channel.code", read_only=True)
    full_name = serializers.CharField(source="bill.payer.full_name", read_only=True)
    payer_ref = serializers.CharField(source="bill.payer.payer_ref", read_only=True)
    terminal_code = serializers.CharField(source="terminal.terminal_id", read_only=True, default=None)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = [
            "id", "payment_ref", "bill", "bill_ref", "channel", "channel_code",
            "amount", "bank_txn_ref", "txn_status", "created_at", "full_name", "payer_ref",
            "terminal", "terminal_code", "posted_by", "posted_by_name",
        ]
        read_only_fields = ["id", "payment_ref", "txn_status", "created_at"]


class PostPaymentSerializer(serializers.Serializer):
    bill_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    channel_code = serializers.ChoiceField(choices=PaymentChannel.CODE_CHOICES, default=PaymentChannel.POS)
    bank_txn_ref = serializers.CharField(required=False, allow_blank=True, default="")
    terminal_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    geo = serializers.DictField(required=False)


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ReceiptSerializer(serializers.ModelSerializer):
    bill_ref = serializers.CharField(source="payment.bill.bill_ref", read_only=True)
    amount = serializers.DecimalField(source="payment.amount", max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Receipt
        fields = ["id", "receipt_ref", "payment", "bill_ref", "amount", "qr_token", "verified_count", "created_at"]
        read_only_fields = fields


class POSTerminalSerializer(serializers.ModelSerializer):
    collected = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = POSTerminal
        fields = ["id", "terminal_id", "bank_terminal_id", "agent", "ward", "status", "collected"]
        read_only_fields = ["id", "collected"]


class APIClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = APIClient
        fields = ["id", "channel", "api_key", "is_active"]
        read_only_fields = ["id", "api_key"]

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.billing.models import Bill
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.api.serializers import (
    APIClientSerializer,
    PaymentSerializer,
    POSTerminalSerializer,
    PostPaymentSerializer,
    ReceiptSerializer,
)
from apps.payments.models import APIClient, PaymentChannel, POSTerminal, Payment, Receipt
from apps.payments.services import PaymentRejected, post_payment
from apps.tenancy.context import find_across_active_councils


class PaymentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)]

    def get_queryset(self):
        qs = Payment.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        return portfolio_filter(qs, self.request, payer_path="bill__payer")

    def get_serializer_class(self):
        return PostPaymentSerializer if self.request.method == "POST" else PaymentSerializer

    def create(self, request, *args, **kwargs):
        serializer = PostPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        bill = get_object_or_404(Bill, pk=data["bill_id"], council_id=request.user.council_id)
        channel, _ = PaymentChannel.objects.get_or_create(code=data["channel_code"])

        try:
            payment = post_payment(
                council_id=request.user.council_id,
                bill=bill,
                channel=channel,
                amount=data["amount"],
                bank_txn_ref=data.get("bank_txn_ref", ""),
                posted_by=request.user,
                geo=data.get("geo"),
            )
        except PaymentRejected as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class ReceiptViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ReceiptSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)]

    def get_queryset(self):
        qs = Receipt.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        return portfolio_filter(qs, self.request, payer_path="payment__bill__payer")


_VerifyReceiptResponseSerializer = inline_serializer(
    "VerifyReceiptResponse",
    {
        "receipt_ref": serializers.CharField(),
        "amount": serializers.DecimalField(max_digits=14, decimal_places=2),
        "bill_ref": serializers.CharField(),
        "payer_name": serializers.CharField(),
        "channel": serializers.CharField(),
        "paid_at": serializers.DateTimeField(),
        "verified_count": serializers.IntegerField(),
    },
)


class VerifyReceiptView(APIView):
    """Public: anyone with a receipt's QR/SMS qr_token can confirm it's real."""

    permission_classes = [AllowAny]

    @extend_schema(responses={200: _VerifyReceiptResponseSerializer, 404: OpenApiResponse(description="Receipt not found")}, tags=["payments"])
    def get(self, request, qr_token):
        def lookup(_council):
            return Receipt.objects.select_related("payment__bill__payer").filter(qr_token=qr_token).first()

        receipt = find_across_active_councils(lookup)
        if receipt is None:
            return Response({"error": "Receipt not found"}, status=status.HTTP_404_NOT_FOUND)

        receipt.verified_count += 1
        receipt.save(update_fields=["verified_count"])
        payment = receipt.payment
        return Response({
            "receipt_ref": receipt.receipt_ref,
            "amount": payment.amount,
            "bill_ref": payment.bill.bill_ref,
            "payer_name": payment.bill.payer.full_name,
            "channel": payment.channel.code,
            "paid_at": payment.created_at,
            "verified_count": receipt.verified_count,
        })


class POSTerminalViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = POSTerminalSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]

    def get_queryset(self):
        return POSTerminal.objects.filter(council_id=self.request.user.council_id).order_by("terminal_id")


class APIClientViewSet(viewsets.ModelViewSet):
    serializer_class = APIClientSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        return APIClient.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        import secrets

        from apps.payments.crypto import encrypt_secret

        secret = secrets.token_urlsafe(32)
        serializer.save(
            council_id=self.request.user.council_id,
            api_key=f"key_{secrets.token_urlsafe(16)}",
            secret_encrypted=encrypt_secret(secret),
        )
        self._plaintext_secret = secret

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data["secret"] = self._plaintext_secret
        response.data["_secret_warning"] = "Shown once — store it now, it cannot be retrieved again."
        return response

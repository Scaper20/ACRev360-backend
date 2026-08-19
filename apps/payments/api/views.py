from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
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
    ReversePaymentSerializer,
)
from apps.payments.models import APIClient, PaymentChannel, POSTerminal, Payment, Receipt
from apps.payments.services import PaymentRejected, post_payment, reverse_payment
from apps.tenancy.context import find_across_active_councils


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by txn_status"),
            OpenApiParameter("channel", OpenApiTypes.STR, description="Filter by channel code"),
            OpenApiParameter("payer", OpenApiTypes.INT, description="Filter to one payer's payments"),
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by payment ref, bill ref or payer name"),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Only payments on/after this date"),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Only payments on/before this date"),
        ]
    )
)
class PaymentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        qs = Payment.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        qs = qs.select_related("bill", "bill__payer", "channel", "terminal", "posted_by")
        qs = portfolio_filter(qs, self.request, payer_path="bill__payer")

        params = self.request.query_params
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(txn_status=status_param)
        channel_param = params.get("channel")
        if channel_param:
            qs = qs.filter(channel__code=channel_param)
        payer_param = params.get("payer")
        if payer_param:
            qs = qs.filter(bill__payer_id=payer_param)
        q = params.get("q")
        if q:
            qs = qs.filter(
                Q(payment_ref__icontains=q) | Q(bill__bill_ref__icontains=q) | Q(bill__payer__full_name__icontains=q)
            )
        date_from = params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def get_serializer_class(self):
        return PostPaymentSerializer if self.request.method == "POST" else PaymentSerializer

    def create(self, request, *args, **kwargs):
        serializer = PostPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        bill = get_object_or_404(Bill, pk=data["bill_id"], council_id=request.user.council_id)
        channel, _ = PaymentChannel.objects.get_or_create(code=data["channel_code"])
        terminal = None
        if data.get("terminal_id") is not None:
            terminal = get_object_or_404(POSTerminal, pk=data["terminal_id"], council_id=request.user.council_id)

        try:
            payment = post_payment(
                council_id=request.user.council_id,
                bill=bill,
                channel=channel,
                terminal=terminal,
                amount=data["amount"],
                bank_txn_ref=data.get("bank_txn_ref", ""),
                posted_by=request.user,
                geo=data.get("geo"),
            )
        except PaymentRejected as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ReversePaymentSerializer, responses=PaymentSerializer)
    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def reverse(self, request, pk=None):
        """Only COUNCIL_ADMIN may reverse a payment — see reverse_payment()."""
        payment = self.get_object()
        serializer = ReversePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payment = reverse_payment(payment=payment, actor=request.user, reason=serializer.validated_data["reason"])
        except PaymentRejected as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentSerializer(payment).data)


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
        return (
            POSTerminal.objects.filter(council_id=self.request.user.council_id)
            .annotate(
                collected=Coalesce(
                    Sum("payments__amount", filter=Q(payments__txn_status=Payment.CONFIRMED)),
                    0,
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
            .order_by("terminal_id")
        )


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

from django.db import IntegrityError
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
from apps.audit.services import audit
from apps.billing.models import Bill
from apps.common.api.views import PlatformWideListMixin
from apps.common.filtering import date_span_bounds, name_search_q, parse_date, parse_int
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
from apps.payments.notifications import send_receipt
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
    # GLOBAL_VIEW deliberately excluded — payments carry payer full_name/payer_ref
    # and posted_by_name, exactly what a stakeholder account must not see.
    # REVENUE_OFFICER is included here (list/retrieve) but excluded again in
    # get_permissions() below for create — read-only, same portfolio as
    # CONSULTANT (see common.scoping.portfolio_filter). `reverse` already
    # declares its own narrower COUNCIL_ADMIN-only permission_classes. The
    # RBAC-expansion additions are read-only for the same reason — see
    # BillViewSet's identical note and docs/RBAC_EXPANSION_DESIGN.md.
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER,
        AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
        AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
    )]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.action == "create":
            return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Payment.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        qs = qs.select_related("bill", "bill__payer", "channel", "terminal", "posted_by")
        qs = qs.prefetch_related("allocations__bill_line__assessment__council_revenue_item")
        qs = portfolio_filter(qs, self.request, payer_path="bill__payer")

        params = self.request.query_params
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(txn_status=status_param)
        channel_param = params.get("channel")
        if channel_param:
            qs = qs.filter(channel__code=channel_param)
        payer_param = parse_int(params, "payer")
        if payer_param is not None:
            qs = qs.filter(bill__payer_id=payer_param)
        q = params.get("q")
        if q:
            qs = qs.filter(
                Q(payment_ref__icontains=q) | Q(bill__bill_ref__icontains=q) | name_search_q(q, prefix="bill__payer")
            )
        # parse_date/parse_int so a malformed value 400s instead of 500ing —
        # raw strings reached .filter() directly before, and Django's ValueError
        # isn't a DRF APIException (same class of bug as the payer param above).
        # date_span_bounds keeps the window on the raw timestamp (sargable)
        # rather than the old `created_at__date__gte/lte` casts.
        date_from = parse_date(params, "date_from")
        if date_from is not None:
            start, _ = date_span_bounds(date_from)
            qs = qs.filter(created_at__gte=start)
        date_to = parse_date(params, "date_to")
        if date_to is not None:
            _, end = date_span_bounds(date_to)
            qs = qs.filter(created_at__lt=end)
        return qs

    def get_serializer_class(self):
        return PostPaymentSerializer if self.request.method == "POST" else PaymentSerializer

    def create(self, request, *args, **kwargs):
        serializer = PostPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The bill must sit inside the caller's own portfolio — RLS only
        # stops cross-council leaks, it doesn't stop an AGENT/CONSULTANT
        # posting a payment onto a bill outside their portfolio, which
        # create's permissions (COUNCIL_ADMIN/CONSULTANT/AGENT) make reachable.
        bill_qs = portfolio_filter(
            Bill.objects.filter(council_id=request.user.council_id), request, payer_path="payer"
        )
        bill = get_object_or_404(bill_qs, pk=data["bill_id"])
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
        except IntegrityError:
            # The partial UNIQUE(channel, bank_txn_ref) on Payment — a bank
            # reference was already recorded for this channel, so nothing was
            # posted this time. post_payment's @transaction.atomic has already
            # rolled back its payment/allocations/receipt, so the bill state is
            # untouched.
            return Response(
                {"error": "This bank transaction reference was already recorded for this channel — nothing was posted again."},
                status=status.HTTP_409_CONFLICT,
            )

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


def _send_receipt_channel_result_serializer(name):
    # A fresh instance per use — DRF fields are bound (mutated) in place by
    # their parent serializer, so reusing one instance for both "email" and
    # "sms" below silently collapsed them into a single field in the
    # generated schema (both bindings landed on the one shared object).
    return inline_serializer(
        name,
        {
            "attempted": serializers.BooleanField(),
            "sent": serializers.BooleanField(required=False),
            "reason": serializers.CharField(required=False),
            "error": serializers.CharField(required=False),
        },
    )


_SendReceiptResponseSerializer = inline_serializer(
    "SendReceiptResponse",
    {
        "email": _send_receipt_channel_result_serializer("SendReceiptEmailResult"),
        "sms": _send_receipt_channel_result_serializer("SendReceiptSmsResult"),
    },
)


@extend_schema_view(
    list=extend_schema(
        parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by receipt ref, bill ref or payer name")]
    )
)
class ReceiptViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ReceiptSerializer
    # GLOBAL_VIEW deliberately excluded — same reasoning as PaymentViewSet.
    # REVENUE_OFFICER is included here (list) but excluded again in
    # get_permissions() below for `send` — read-only, same portfolio as
    # CONSULTANT (see common.scoping.portfolio_filter). RBAC-expansion
    # additions, same read-only reasoning as PaymentViewSet.
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER,
        AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
        AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
    )]
    # Numeric-only URL matching, same as PaymentViewSet/PayerViewSet/APIClientViewSet —
    # a non-numeric id 404s cleanly at routing instead of reaching get_object().
    # (drf-spectacular types path-param ids as string regardless of this; every
    # frontend call site already wraps the id in String(...) to match.)
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.action != "list":
            return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)()]
        return super().get_permissions()

    def get_queryset(self):
        # select_related for the to-one hops the serializer already walks
        # (bill_ref, full_name, amount); prefetch_related for `lines` — a
        # to-many hop off Bill, select_related can't cover that one. Neither
        # existed before; ReceiptSerializer.lines (new) would otherwise add
        # its own N+1 on top of ones already latent here.
        qs = (
            Receipt.objects.filter(council_id=self.request.user.council_id)
            .select_related("payment__bill__payer")
            .prefetch_related(
                "payment__bill__lines",
                "payment__allocations__bill_line__assessment__council_revenue_item",
            )
            .order_by("-created_at")
        )
        qs = portfolio_filter(qs, self.request, payer_path="payment__bill__payer")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(receipt_ref__icontains=q) | Q(payment__bill__bill_ref__icontains=q)
                | name_search_q(q, prefix="payment__bill__payer")
            )
        return qs

    @extend_schema(request=None, responses=_SendReceiptResponseSerializer)
    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Emails/SMSes the receipt to whatever contact info the payer has on
        file. Neither channel is required to succeed — see
        apps.payments.notifications.send_receipt for why."""
        receipt = self.get_object()
        result = send_receipt(receipt)
        audit(
            council_id=receipt.council_id, actor=request.user, action="RECEIPT_SENT", entity_type="RECEIPT",
            entity_id=receipt.id, detail={"receipt_ref": receipt.receipt_ref, **result},
        )
        return Response(result)


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


class APIClientViewSet(PlatformWideListMixin, viewsets.ModelViewSet):
    """DEVOPS_ADMIN (platform tier, council=null — docs/RBAC_EXPANSION_DESIGN.md)
    reads/manages integration keys across every active council, matching the
    matrix's "API keys, integration configs... no direct business-data edit
    rights needed" — it never gets access_level_permission on any billing/
    payment/payer viewset, only this one."""

    serializer_class = APIClientSerializer
    # DEVOPS_ADMIN is platform tier (council=null) — creating a key means
    # picking a specific council to issue it for, which this endpoint has no
    # UX for from a councilless caller, so DEVOPS_ADMIN stays read/revoke
    # only (see get_permissions). Issuing a new key for a given council stays
    # that council's own COUNCIL_ADMIN's job.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.action == "create":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.DEVOPS_ADMIN)()]

    def get_platform_queryset_fn(self, council_id):
        return APIClient.objects.filter(council_id=council_id)

    def get_queryset(self):
        user = self.request.user
        if user.council_id is None:
            # Platform tier: materialize per council via PlatformWideListMixin,
            # never a lazy queryset evaluated outside a council RLS context.
            return APIClient.objects.none()
        return APIClient.objects.filter(council_id=user.council_id)

    def perform_create(self, serializer):
        import secrets

        from apps.payments.crypto import encrypt_secret

        secret = secrets.token_urlsafe(32)
        client = serializer.save(
            council_id=self.request.user.council_id,
            api_key=f"key_{secrets.token_urlsafe(16)}",
            secret_encrypted=encrypt_secret(secret),
        )
        self._plaintext_secret = secret
        audit(
            council_id=self.request.user.council_id, actor=self.request.user, action="API_CLIENT_CREATED",
            entity_type="API_CLIENT", entity_id=client.id,
            detail={"channel": client.channel.code, "expires_at": str(client.expires_at), "scopes": client.scopes},
        )

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data["secret"] = self._plaintext_secret
        response.data["_secret_warning"] = "Shown once — store it now, it cannot be retrieved again."
        return response

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """The clean, audited way to deactivate a key — is_active is already
        the revocation flag (see APIClient), this just exposes flipping it
        through a real endpoint instead of a raw DB update."""
        client = self.get_object()
        if client.is_active:
            client.is_active = False
            client.save(update_fields=["is_active"])
            audit(
                council_id=request.user.council_id, actor=request.user, action="API_CLIENT_REVOKED",
                entity_type="API_CLIENT", entity_id=client.id, detail={"channel": client.channel.code},
            )
        return Response(APIClientSerializer(client).data)

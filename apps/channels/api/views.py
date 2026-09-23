from django.conf import settings
from django.core.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.billing.models import Bill
from apps.channels import adapters
from apps.channels.api.serializers import (
    ChannelCatalogueEntrySerializer,
    OTCSettlementResponseSerializer,
    OTCSettlementRowSerializer,
    USSDSessionRequestSerializer,
    WebhookResponseSerializer,
)
from apps.channels.services import WebhookAuthError, authenticate_webhook_client
from apps.common.permissions import access_level_permission
from apps.payments.models import ChannelTransactionFeed, PaymentChannel, Receipt
from apps.payments.services import PaymentRejected, post_payment
from apps.tenancy.context import council_context, find_across_active_councils, resolve_council_from_bill_ref


class ChannelCatalogueView(APIView):
    """Public: codes, modes and required fields."""

    permission_classes = [AllowAny]

    @extend_schema(responses=ChannelCatalogueEntrySerializer(many=True), tags=["channels"])
    def get(self, request):
        channels_by_code = {c.code: c.id for c in PaymentChannel.objects.all()}
        return Response([
            {
                "id": channels_by_code.get(code),
                "code": code,
                "label": label,
                # CASH has no webhook payload contract — it's recorded directly, not via a bank feed.
                "required_fields": adapters.REQUIRED_FIELDS.get(code, []),
            }
            for code, label in PaymentChannel.CODE_CHOICES
        ])


class WebhookView(APIView):
    """POST /api/v1/channels/<code>/webhook — all real-time channels. Public by
    transport (any bank/gateway can call it), but every request is signature-
    verified (HMAC, on by default — see V2_ARCHITECTURE.md §8) before it can move
    money."""

    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=WebhookResponseSerializer,
        description="Payload shape is channel-specific — see GET /api/v1/channels for the required-fields matrix per code.",
        tags=["channels"],
    )
    def post(self, request, code):
        code = code.upper()
        payload = request.data

        try:
            adapters.validate(code, payload)
        except adapters.AdapterError as exc:
            return Response({"status": "rejected", "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        normalised = adapters.normalise(code, payload)
        council = resolve_council_from_bill_ref(normalised["bill_ref"])
        if council is None:
            return Response({"status": "rejected", "error": "Unrecognised bill reference"}, status=status.HTTP_400_BAD_REQUEST)

        with council_context(council.id):
            channel, _ = PaymentChannel.objects.get_or_create(code=code)

            if settings.WEBHOOK_STRICT_SIGNATURES:
                signature = request.headers.get("X-ACRev360-Signature")
                try:
                    authenticate_webhook_client(
                        council=council, channel=channel, raw_body=request.body, signature_header=signature,
                    )
                except WebhookAuthError as exc:
                    return Response({"status": "rejected", "error": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

            existing = ChannelTransactionFeed.objects.filter(channel=channel, bank_txn_ref=normalised["bank_txn_ref"]).first()
            if existing:
                return Response({"status": "duplicate", "bank_txn_ref": normalised["bank_txn_ref"]}, status=status.HTTP_200_OK)

            # get_or_create keeps replay idempotency race-free: two nearly-
            # simultaneous replays of the same ref both pass the check above,
            # but UNIQUE(channel, bank_txn_ref) lets only one create — the
            # loser gets the existing row back (no IntegrityError → no 500).
            feed_row, created = ChannelTransactionFeed.objects.get_or_create(
                council_id=council.id, channel=channel, bank_txn_ref=normalised["bank_txn_ref"],
                defaults={"amount": normalised["amount"], "raw_payload": payload},
            )
            if not created:
                return Response({"status": "duplicate", "bank_txn_ref": normalised["bank_txn_ref"]}, status=status.HTTP_200_OK)

            bill = Bill.objects.filter(bill_ref=normalised["bill_ref"]).first()
            if bill is None:
                feed_row.match_status = ChannelTransactionFeed.EXCEPTION
                feed_row.save(update_fields=["match_status"])
                return Response({"status": "accepted_unmatched", "bank_txn_ref": normalised["bank_txn_ref"]}, status=status.HTTP_202_ACCEPTED)

            try:
                payment = post_payment(
                    council_id=council.id, bill=bill, channel=channel,
                    amount=normalised["amount"], bank_txn_ref=normalised["bank_txn_ref"],
                )
            except PaymentRejected as exc:
                feed_row.match_status = ChannelTransactionFeed.EXCEPTION
                feed_row.save(update_fields=["match_status"])
                return Response({"status": "rejected", "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            feed_row.match_status = ChannelTransactionFeed.MATCHED
            feed_row.matched_payment = payment
            feed_row.save(update_fields=["match_status", "matched_payment"])

            return Response({
                "status": "posted",
                "paymentRef": payment.payment_ref,
                "receiptRef": payment.receipt.receipt_ref,
                "verifyToken": str(payment.receipt.qr_token),
            }, status=status.HTTP_201_CREATED)


class OTCSettlementView(APIView):
    """POST /api/v1/channels/OTC/settlement — end-of-day teller settlement file.
    Safe to re-send: already-received references are skipped, not re-posted."""

    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]

    @extend_schema(request=OTCSettlementRowSerializer(many=True), responses=OTCSettlementResponseSerializer, tags=["channels"])
    def post(self, request):
        if not isinstance(request.data, list):
            return Response(
                {"error": "Expected a JSON array of settlement rows."}, status=status.HTTP_400_BAD_REQUEST
            )
        rows = request.data
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        posted, duplicates_skipped, exceptions = 0, 0, []

        for row in rows:
            try:
                adapters.validate(PaymentChannel.OTC, row)
            except adapters.AdapterError as exc:
                exceptions.append({"row": row, "error": str(exc)})
                continue
            normalised = adapters.normalise(PaymentChannel.OTC, row)

            feed_row, created = ChannelTransactionFeed.objects.get_or_create(
                council_id=request.user.council_id, channel=channel, bank_txn_ref=normalised["bank_txn_ref"],
                defaults={"amount": normalised["amount"], "raw_payload": row},
            )
            if not created:
                # Safe to re-send: same-ref rows are skipped, never re-posted.
                duplicates_skipped += 1
                continue
            bill = Bill.objects.filter(bill_ref=normalised["bill_ref"], council_id=request.user.council_id).first()
            if bill is None:
                feed_row.match_status = ChannelTransactionFeed.EXCEPTION
                feed_row.save(update_fields=["match_status"])
                exceptions.append({"bill_ref": normalised["bill_ref"], "error": "Bill not found"})
                continue
            try:
                payment = post_payment(
                    council_id=request.user.council_id, bill=bill, channel=channel,
                    amount=normalised["amount"], bank_txn_ref=normalised["bank_txn_ref"], posted_by=request.user,
                )
            except PaymentRejected as exc:
                feed_row.match_status = ChannelTransactionFeed.EXCEPTION
                feed_row.save(update_fields=["match_status"])
                exceptions.append({"bill_ref": normalised["bill_ref"], "error": str(exc)})
                continue

            feed_row.match_status = ChannelTransactionFeed.MATCHED
            feed_row.matched_payment = payment
            feed_row.save(update_fields=["match_status", "matched_payment"])
            posted += 1

        return Response({"posted": posted, "duplicates_skipped": duplicates_skipped, "exceptions": exceptions})


class USSDSessionView(APIView):
    """Stateless menu driven entirely by the accumulated input string a telco
    gateway sends per keypress. 1 check balance, 2 verify a receipt. Bill payment
    via USSD is disabled until a payment gateway integration (HMAC client +
    feed rows) is in place."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=USSDSessionRequestSerializer,
        responses=OpenApiResponse(response=OpenApiTypes.STR, description="Raw text/plain: 'CON ...' to continue the session, 'END ...' to close it."),
        tags=["channels"],
    )
    def post(self, request):
        text = request.data.get("text", "")
        parts = text.split("*") if text else []

        if not parts or parts[0] == "":
            return self._plain("CON Welcome to ACRev360\n1. Check balance\n2. Verify a receipt")

        option = parts[0]

        if option == "1":
            if len(parts) < 2:
                return self._plain("CON Enter bill reference\ne.g. 1*KAC/2026/000001")
            bill_ref = parts[1]
            council = resolve_council_from_bill_ref(bill_ref)
            if council is None:
                return self._plain("END Bill reference not found.")
            with council_context(council.id):
                bill = Bill.objects.filter(bill_ref=bill_ref).first()
                if bill is None:
                    return self._plain("END Bill reference not found.")
                return self._plain(f"END Balance for {bill.bill_ref}: NGN {bill.balance}")

        if option == "2":
            if len(parts) < 2:
                return self._plain("CON Enter receipt verification code")
            token = parts[1]

            def lookup(_council):
                return Receipt.objects.filter(qr_token=token).first()

            try:
                receipt = find_across_active_councils(lookup)
            except (ValueError, ValidationError):
                receipt = None
            if receipt is None:
                return self._plain("END Receipt not found.")
            return self._plain(f"END Receipt {receipt.receipt_ref} verified. Amount: NGN {receipt.payment.amount}")

        return self._plain("END Invalid option.")

    @staticmethod
    def _plain(body: str) -> Response:
        return Response(body, content_type="text/plain")

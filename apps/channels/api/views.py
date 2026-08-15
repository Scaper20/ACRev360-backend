from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
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
from apps.common.permissions import access_level_permission
from apps.payments.crypto import decrypt_secret
from apps.payments.models import APIClient, ChannelTransactionFeed, PaymentChannel, Receipt
from apps.payments.services import PaymentRejected, post_payment
from apps.tenancy.context import council_context, find_across_active_councils, resolve_council_from_bill_ref


class ChannelCatalogueView(APIView):
    """Public: codes, modes and required fields."""

    permission_classes = [AllowAny]

    @extend_schema(responses=ChannelCatalogueEntrySerializer(many=True), tags=["channels"])
    def get(self, request):
        channels_by_code = {c.code: c.id for c in PaymentChannel.objects.all()}
        return Response([
            {"id": channels_by_code.get(code), "code": code, "label": label, "required_fields": adapters.REQUIRED_FIELDS[code]}
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
                clients = APIClient.objects.filter(council=council, channel=channel, is_active=True)
                verified = any(
                    adapters.verify_signature(decrypt_secret(c.secret_encrypted), request.body, signature)
                    for c in clients
                )
                if not verified:
                    return Response({"status": "rejected", "error": "Signature verification failed"}, status=status.HTTP_401_UNAUTHORIZED)

            existing = ChannelTransactionFeed.objects.filter(channel=channel, bank_txn_ref=normalised["bank_txn_ref"]).first()
            if existing:
                return Response({"status": "duplicate", "bank_txn_ref": normalised["bank_txn_ref"]}, status=status.HTTP_200_OK)

            feed_row = ChannelTransactionFeed.objects.create(
                council_id=council.id, channel=channel, bank_txn_ref=normalised["bank_txn_ref"],
                amount=normalised["amount"], raw_payload=payload,
            )

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
        rows = request.data if isinstance(request.data, list) else []
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        posted, duplicates_skipped, exceptions = 0, 0, []

        for row in rows:
            try:
                adapters.validate(PaymentChannel.OTC, row)
            except adapters.AdapterError as exc:
                exceptions.append({"row": row, "error": str(exc)})
                continue
            normalised = adapters.normalise(PaymentChannel.OTC, row)

            if ChannelTransactionFeed.objects.filter(channel=channel, bank_txn_ref=normalised["bank_txn_ref"]).exists():
                duplicates_skipped += 1
                continue

            feed_row = ChannelTransactionFeed.objects.create(
                council_id=request.user.council_id, channel=channel, bank_txn_ref=normalised["bank_txn_ref"],
                amount=normalised["amount"], raw_payload=row,
            )
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
    gateway sends per keypress. 1 pay a bill, 2 check balance, 3 verify a receipt."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=USSDSessionRequestSerializer,
        responses=OpenApiResponse(response=OpenApiTypes.STR, description="Raw text/plain: 'CON ...' to continue the session, 'END ...' to close it."),
        tags=["channels"],
    )
    def post(self, request):
        text = request.data.get("text", "")
        msisdn = request.data.get("msisdn", "")
        parts = text.split("*") if text else []

        if not parts or parts[0] == "":
            return self._plain("CON Welcome to ACRev360\n1. Pay a bill\n2. Check balance\n3. Verify a receipt")

        option = parts[0]

        if option == "1":
            if len(parts) < 3:
                return self._plain("CON Enter bill reference then amount, separated by *\ne.g. 1*KAC/2026/000001*5000")
            bill_ref, amount_str = parts[1], parts[2]
            council = resolve_council_from_bill_ref(bill_ref)
            if council is None:
                return self._plain("END Bill reference not found.")
            with council_context(council.id):
                bill = Bill.objects.filter(bill_ref=bill_ref).first()
                if bill is None:
                    return self._plain("END Bill reference not found.")
                try:
                    amount = Decimal(amount_str)
                except InvalidOperation:
                    return self._plain("END Invalid amount.")
                channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.USSD)
                try:
                    payment = post_payment(
                        council_id=council.id, bill=bill, channel=channel, amount=amount,
                        bank_txn_ref=f"USSD-{msisdn}-{int(timezone.now().timestamp())}",
                    )
                except PaymentRejected as exc:
                    return self._plain(f"END Payment failed: {exc}")
                return self._plain(f"END Payment received. Receipt: {payment.receipt.receipt_ref}")

        if option == "2":
            if len(parts) < 2:
                return self._plain("CON Enter bill reference\ne.g. 2*KAC/2026/000001")
            bill_ref = parts[1]
            council = resolve_council_from_bill_ref(bill_ref)
            if council is None:
                return self._plain("END Bill reference not found.")
            with council_context(council.id):
                bill = Bill.objects.filter(bill_ref=bill_ref).first()
                if bill is None:
                    return self._plain("END Bill reference not found.")
                return self._plain(f"END Balance for {bill.bill_ref}: NGN {bill.balance}")

        if option == "3":
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

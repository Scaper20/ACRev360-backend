import datetime

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole, FieldAgent
from apps.billing.models import Assessment, Bill
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.models import Payment, PaymentChannel
from apps.registry.models import Payer
from apps.settlements.models import CommissionSettlement

_TREND_DAYS = 14
_TOP_ITEMS = 5

_SummaryResponseSerializer = inline_serializer(
    "DashboardSummaryResponse",
    {
        "billed": serializers.DecimalField(max_digits=14, decimal_places=2),
        "collected": serializers.DecimalField(max_digits=14, decimal_places=2),
        "outstanding": serializers.DecimalField(max_digits=14, decimal_places=2),
        "bills_by_status": inline_serializer(
            "BillsByStatus", {"status": serializers.CharField(), "count": serializers.IntegerField()}, many=True
        ),
        "bills": serializers.IntegerField(),
        "assessments": serializers.IntegerField(),
        "payers": serializers.IntegerField(),
        "active_agents": serializers.IntegerField(),
        "by_channel": inline_serializer(
            "CollectedByChannel",
            {
                "code": serializers.CharField(),
                "label": serializers.CharField(),
                "amount": serializers.DecimalField(max_digits=14, decimal_places=2),
            },
            many=True,
        ),
        "by_item": inline_serializer(
            "BilledByItem",
            {
                "item_name": serializers.CharField(),
                "billed": serializers.DecimalField(max_digits=14, decimal_places=2),
            },
            many=True,
        ),
        "trend": inline_serializer(
            "CollectionsTrend",
            {"d": serializers.DateField(), "amount": serializers.DecimalField(max_digits=14, decimal_places=2)},
            many=True,
        ),
    },
)

_GlobalResponseSerializer = inline_serializer(
    "DashboardGlobalResponse",
    {
        "by_consultant": inline_serializer(
            "CollectedByConsultant",
            {
                "consultant_name": serializers.CharField(),
                "collected": serializers.DecimalField(max_digits=14, decimal_places=2),
                "billed": serializers.DecimalField(max_digits=14, decimal_places=2),
                "collection_rate": serializers.IntegerField(allow_null=True),
                "commission_accrued": serializers.DecimalField(max_digits=14, decimal_places=2),
                "status": serializers.CharField(allow_null=True),
            },
            many=True,
        ),
        "by_ward": inline_serializer(
            "CollectedByWard",
            {
                "ward_name": serializers.CharField(),
                "collected": serializers.DecimalField(max_digits=14, decimal_places=2),
                "payers": serializers.IntegerField(),
            },
            many=True,
        ),
    },
)


class DashboardSummaryView(APIView):
    """Scoped to the caller's portfolio — see V2_ARCHITECTURE.md §5.
    `billed` is `total_amount - arrears_amount`, matching API_REFERENCE.md: an
    arrears segment rolled forward from a consolidation isn't counted as new
    billing, since it was already billed once on the bill it came from."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiResponse(_SummaryResponseSerializer), tags=["dashboard"])
    def get(self, request):
        bills = Bill.objects.filter(council_id=request.user.council_id).exclude(
            status__in=[Bill.SUPERSEDED, Bill.CANCELLED]
        )
        bills = portfolio_filter(bills, request)
        billed = bills.aggregate(total=Sum(F("total_amount") - F("arrears_amount")))["total"] or 0

        payments = Payment.objects.filter(council_id=request.user.council_id, txn_status=Payment.CONFIRMED)
        payments = portfolio_filter(payments, request, payer_path="bill__payer")
        collected = payments.aggregate(total=Sum("amount"))["total"] or 0

        by_status = list(bills.values("status").annotate(count=Count("id")).order_by("status"))

        # Tied to bill_lines__bill__in=bills (not just status=BILLED) so this
        # count moves in lockstep with `bills` — an Assessment's status stays
        # BILLED even after its bill is superseded/cancelled, so status alone
        # would let this diverge from the bills count it's meant to pair with.
        assessments = Assessment.objects.filter(
            council_id=request.user.council_id, status=Assessment.BILLED, bill_lines__bill__in=bills,
        ).distinct()
        assessments = portfolio_filter(assessments, request)

        # payer_path="" because Payer is itself the payer here, not a relation
        # to walk through (portfolio_filter's default assumes the latter).
        payers = portfolio_filter(Payer.objects.filter(council_id=request.user.council_id), request, payer_path="")

        active_agents = FieldAgent.objects.filter(council_id=request.user.council_id, status=FieldAgent.ACTIVE)
        if request.user.access_level == AppRole.CONSULTANT:
            active_agents = active_agents.filter(user__consultant_id=request.user.consultant_id)

        channel_labels = dict(PaymentChannel.CODE_CHOICES)
        by_channel = [
            {
                "code": row["channel__code"],
                "label": channel_labels.get(row["channel__code"], row["channel__code"]),
                "amount": row["amount"],
            }
            for row in payments.values("channel__code").annotate(amount=Sum("amount")).order_by("-amount")
        ]

        by_item = [
            {"item_name": row["lines__assessment__council_revenue_item__item_name"], "billed": row["billed"]}
            for row in (
                bills.exclude(lines__isnull=True)
                .values("lines__assessment__council_revenue_item__item_name")
                .annotate(billed=Sum("lines__line_amount"))
                .order_by("-billed")[:_TOP_ITEMS]
            )
        ]

        today = timezone.localdate()
        start = today - datetime.timedelta(days=_TREND_DAYS - 1)
        trend_raw = {
            row["d"]: row["amount"]
            for row in (
                payments.filter(created_at__date__gte=start)
                .annotate(d=TruncDate("created_at"))
                .values("d")
                .annotate(amount=Sum("amount"))
            )
        }
        trend = [
            {
                "d": start + datetime.timedelta(days=i),
                "amount": trend_raw.get(start + datetime.timedelta(days=i), 0),
            }
            for i in range(_TREND_DAYS)
        ]

        return Response({
            "billed": billed,
            "collected": collected,
            "outstanding": billed - collected,
            "bills_by_status": by_status,
            "bills": bills.count(),
            "assessments": assessments.count(),
            "payers": payers.count(),
            "active_agents": active_agents.count(),
            "by_channel": by_channel,
            "by_item": by_item,
            "trend": trend,
        })


class DashboardGlobalView(APIView):
    """Council admin / global view only — billed vs. collected by consultant (or
    Council Direct), matching the prototype's v_global_performance view."""

    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.GLOBAL_VIEW)]

    @extend_schema(responses=OpenApiResponse(_GlobalResponseSerializer), tags=["dashboard"])
    def get(self, request):
        payments = Payment.objects.filter(council_id=request.user.council_id, txn_status=Payment.CONFIRMED)
        by_consultant = (
            payments.values("bill__payer__enumerated_by__consultant__consultant_name")
            .annotate(collected=Sum("amount"))
            .order_by("-collected")
        )

        bills = Bill.objects.filter(council_id=request.user.council_id).exclude(
            status__in=[Bill.SUPERSEDED, Bill.CANCELLED]
        )
        billed_by_consultant = bills.values(
            "payer__enumerated_by__consultant__consultant_name",
            "payer__enumerated_by__consultant__status",
        ).annotate(billed=Sum(F("total_amount") - F("arrears_amount")))
        billed_map = {
            row["payer__enumerated_by__consultant__consultant_name"]: row["billed"] for row in billed_by_consultant
        }
        status_map = {
            row["payer__enumerated_by__consultant__consultant_name"]: row["payer__enumerated_by__consultant__status"]
            for row in billed_by_consultant
        }

        accrued_by_consultant = (
            CommissionSettlement.objects.filter(
                council_id=request.user.council_id,
                status__in=[CommissionSettlement.COMPUTED, CommissionSettlement.APPROVED],
            )
            .values("consultant__consultant_name")
            .annotate(accrued=Sum("commission_amount"))
        )
        accrued_map = {row["consultant__consultant_name"]: row["accrued"] for row in accrued_by_consultant}

        rows = []
        for row in by_consultant:
            raw_name = row["bill__payer__enumerated_by__consultant__consultant_name"]
            collected = row["collected"]
            billed = billed_map.get(raw_name, 0)
            rows.append({
                "consultant_name": raw_name or "Council Direct",
                "collected": collected,
                "billed": billed,
                "collection_rate": round(collected / billed * 100) if billed else None,
                "commission_accrued": accrued_map.get(raw_name, 0),
                "status": status_map.get(raw_name),
            })

        by_ward = (
            payments.values("bill__payer__ward__ward_name")
            .annotate(collected=Sum("amount"))
            .order_by("-collected")
        )
        payers_by_ward = (
            Payer.objects.filter(council_id=request.user.council_id)
            .values("ward__ward_name")
            .annotate(payers=Count("id"))
        )
        payers_map = {row["ward__ward_name"]: row["payers"] for row in payers_by_ward}
        ward_rows = [
            {
                "ward_name": row["bill__payer__ward__ward_name"],
                "collected": row["collected"],
                "payers": payers_map.get(row["bill__payer__ward__ward_name"], 0),
            }
            for row in by_ward
        ]

        return Response({"by_consultant": rows, "by_ward": ward_rows})

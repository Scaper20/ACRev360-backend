from django.db.models import Count, F, Sum
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.billing.models import Bill
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.models import Payment

_SummaryResponseSerializer = inline_serializer(
    "DashboardSummaryResponse",
    {
        "billed": serializers.DecimalField(max_digits=14, decimal_places=2),
        "collected": serializers.DecimalField(max_digits=14, decimal_places=2),
        "outstanding": serializers.DecimalField(max_digits=14, decimal_places=2),
        "bills_by_status": inline_serializer(
            "BillsByStatus", {"status": serializers.CharField(), "count": serializers.IntegerField()}, many=True
        ),
    },
)

_GlobalResponseSerializer = inline_serializer(
    "DashboardGlobalResponse",
    {
        "by_consultant": inline_serializer(
            "CollectedByConsultant",
            {"consultant_name": serializers.CharField(), "collected": serializers.DecimalField(max_digits=14, decimal_places=2)},
            many=True,
        ),
        "by_ward": inline_serializer(
            "CollectedByWard",
            {"ward_name": serializers.CharField(), "collected": serializers.DecimalField(max_digits=14, decimal_places=2)},
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

        return Response({
            "billed": billed,
            "collected": collected,
            "outstanding": billed - collected,
            "bills_by_status": by_status,
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
        rows = [
            {
                "consultant_name": row["bill__payer__enumerated_by__consultant__consultant_name"] or "Council Direct",
                "collected": row["collected"],
            }
            for row in by_consultant
        ]

        by_ward = (
            payments.values("bill__payer__ward__ward_name")
            .annotate(collected=Sum("amount"))
            .order_by("-collected")
        )
        ward_rows = [
            {"ward_name": row["bill__payer__ward__ward_name"], "collected": row["collected"]}
            for row in by_ward
        ]

        return Response({"by_consultant": rows, "by_ward": ward_rows})
